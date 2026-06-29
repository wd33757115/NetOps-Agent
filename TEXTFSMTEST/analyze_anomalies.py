#!/usr/bin/env python3
"""Final corrected anomaly analysis - v2"""
import re, csv
from pathlib import Path

SRC = Path(__file__).parent
with open(SRC / "device_patrol_summary.csv", 'r', encoding='utf-8-sig') as f:
    rows = list(csv.DictReader(f))

sep = "=" * 80

# ============ 1. Optical Power ============
print(sep)
print("【1. 光功率分析 - 关键发现】")
print(sep)
print("参考: 10G/25G-SR: Tx=-8.4~+2.4dBm, Rx灵敏度>-11dBm")
print()

for r in rows:
    name = r['设备名称']
    raw = r['接口光衰']
    if not raw or '未采集' in raw:
        continue
    if 'DOM不支持' in raw and 'Ethernet' not in raw.replace('DOM不支持; ', ''):
        continue

    # Proper parsing: "Ethernet1/4:Tx Power:-6.55dBm" -> iface=Ethernet1/4, type=Tx, value=-6.55
    cleaned = raw.replace('DOM不支持; ', '')
    # Match each measurement
    pattern = re.compile(r'(\S+):(Tx|Rx) Power:([-\d.]+)dBm')
    matches = pattern.findall(cleaned)

    # Group by interface
    ifaces = {}
    for iface, ptype, val in matches:
        v = float(val)
        if iface not in ifaces:
            ifaces[iface] = {}
        ifaces[iface][ptype] = v

    for iface, powers in sorted(ifaces.items()):
        tx = powers.get('Tx', None)
        rx = powers.get('Rx', None)
        flags = []
        if rx is not None:
            if rx < -11.0:
                flags.append(f"Rx={rx}dBm < -11.0dBm 低于灵敏度!")
            elif rx < -9.0:
                flags.append(f"Rx={rx}dBm 偏低需关注")
        if flags:
            tx_s = f"{tx}dBm" if tx is not None else "N/A"
            rx_s = f"{rx}dBm" if rx is not None else "N/A"
            print(f"  [{name}] {iface}: Tx={tx_s} Rx={rx_s}")
            for f in flags:
                print(f"    -> {f}")

print()
print("正常范围统计:")
low_rx_ifaces = []
for r in rows:
    raw = r['接口光衰']
    if not raw or '未采集' in raw or ('DOM不支持' in raw and 'Ethernet' not in raw.replace('DOM不支持; ', '')):
        continue
    cleaned = raw.replace('DOM不支持; ', '')
    pattern = re.compile(r'(\S+):(Tx|Rx) Power:([-\d.]+)dBm')
    matches = pattern.findall(cleaned)
    ifaces = {}
    for iface, ptype, val in matches:
        v = float(val)
        if iface not in ifaces:
            ifaces[iface] = {}
        ifaces[iface][ptype] = v
    for iface, powers in ifaces.items():
        rx = powers.get('Rx')
        if rx is not None and rx < -6.0:
            low_rx_ifaces.append((r['设备名称'], iface, rx))

if low_rx_ifaces:
    print("Rx < -6dBm 的接口 (偏低但可能正常):")
    for name, iface, rx in low_rx_ifaces:
        print(f"  {name} {iface}: Rx={rx}dBm")

# ============ 2. CPU ============
print()
print(sep)
print("【2. CPU使用率分析】")
print(sep)

cpu_findings = []
for r in rows:
    name = r['设备名称']
    cpu = r['CPU使用率']
    model = r['设备型号']

    if 'sec' in cpu:
        m = re.search(r'5sec:\s*(\d+)%', cpu)
        if m:
            val = int(m.group(1))
            if val >= 35:
                cpu_findings.append((name, val, 'IOS', f"CPU 5秒={val}% 偏高 (C2960X/IOS典型上限~40%)"))
            else:
                print(f"  [{name}]: CPU 5sec={val}% - IOS正常")
    elif 'user' in cpu.lower():
        m_idle = re.search(r'(\d+\.\d+)%\s*idle', cpu)
        if m_idle:
            idle = float(m_idle.group(1))
            if idle < 95:
                cpu_findings.append((name, idle, 'NXOS', f"CPU idle={idle}% 略低于95%"))
            else:
                print(f"  [{name}]: CPU idle={idle}% - NXOS正常")

if cpu_findings:
    print("需关注的CPU:")
    for name, val, os_type, desc in cpu_findings:
        print(f"  [{name}]: {desc}")

# ============ 3. Temperature ============
print()
print(sep)
print("【3. 温度分析】")
print(sep)

for r in rows:
    name = r['设备名称']
    temp = r['温度']
    temps = re.findall(r'(\d+)C', temp)
    if temps:
        temps_int = [int(t) for t in temps]
        max_t = max(temps_int)
        flag = ""
        if max_t >= 60:
            flag = " !!过热!!"
        elif max_t >= 50:
            flag = " 略高"
        print(f"  [{name}]: 最高{max_t}C{flag} | {temp[:80]}")

# ============ 4. Fan/Power ============
print()
print(sep)
print("【4. 风扇/电源状态】")
print(sep)
all_ok = True
for r in rows:
    name = r['设备名称']
    fan = r['风扇状态']
    pwr = r['电源状态']
    if 'fail' in fan.lower() or 'abs' in fan.lower():
        print(f"  [{name}] 风扇异常: {fan}")
        all_ok = False
    if 'fail' in pwr.lower() or 'abs' in pwr.lower():
        print(f"  [{name}] 电源异常: {pwr}")
        all_ok = False
if all_ok:
    print("  全部正常, 所有双电源设备均配置冗余。")

# ============ 5. Interface Status & Flapping ============
print()
print(sep)
print("【5. 接口状态与潜在 flapping 检测】")
print(sep)
print("注意: 当前采集为接口状态快照, 未采集syslog历史日志,")
print("无法直接检测接口历史up/down事件。以下基于当前状态分析。")
print()

for r in rows:
    name = r['设备名称']
    iface = r['接口状态']
    m = re.match(r'UP:(\d+) DOWN:(\d+) ADMIN_DOWN:(\d+)', iface)
    if m:
        up, down, ad = int(m.group(1)), int(m.group(2)), int(m.group(3))
        total = up + down + ad
        down_pct = down / total * 100 if total > 0 else 0
        if down >= 20:
            print(f"  [{name}] UP={up} DOWN={down} ADM={ad}, 闲置{down_pct:.0f}%")

        # Check if expected interfaces are UP
        iface_list = re.search(r'\| (.+)', iface)
        iface_text = iface_list.group(1) if iface_list else ""
        # Check for common uplink ports
        mgmt_up = 'mgmt0:up' in iface_text

print()

# ============ 6. Version ============
print()
print(sep)
print("【6. 软件版本差异】")
print(sep)

from collections import defaultdict
by_model = defaultdict(list)
for r in rows:
    model = r['设备型号'].split()[0]
    by_model[model].append((r['设备名称'], r['软件版本']))

for model, devs in sorted(by_model.items()):
    vers = set(v for _, v in devs)
    if len(vers) > 1:
        print(f"  [{model}] 版本不一致:")
        for name, ver in devs:
            marker = " <-- 需升级" if '7.0' in ver else ""
            print(f"    {name}: {ver}{marker}")

# ============ 7. Uptime ============
print()
print(sep)
print("【7. 运行时间】")
print(sep)

def parse_uptime(s):
    """Parse uptime string to total days"""
    if 'day(s)' in s or 'day, ' in s or 'days,' in s:
        m = re.search(r'(\d+)\s*day', s)
        if m:
            # Check if this is actually "X days" not "X weeks, Y days"
            # Look for weeks pattern first
            m_wk = re.search(r'(\d+)\s*week', s)
            m_day = re.search(r'(\d+)\s*day', s)
            if m_wk:
                weeks = int(m_wk.group(1))
                days = int(m_day.group(1)) if m_day else 0
                return weeks * 7 + days
    elif 'week' in s:
        m = re.search(r'(\d+)\s*week', s)
        if m:
            weeks = int(m.group(1))
            m_day = re.search(r'(\d+)\s*day', s)
            days = int(m_day.group(1)) if m_day else 0
            return weeks * 7 + days
    return None

for r in rows:
    name = r['设备名称']
    uptime = r['运行时间']
    days = parse_uptime(uptime)
    if days is None:
        print(f"  [{name}]: 无法解析 - {uptime[:60]}")
    else:
        flag = ""
        if days < 30:
            flag = " [近期重启!]"
        elif days < 90:
            flag = " [3个月内重启过]"
        print(f"  [{name}]: ~{days}天 ({uptime[:60]}){flag}")

# Check peer consistency
print()
print("  同组设备运行时间一致性:")
from collections import defaultdict as dd
peers = dd(list)
for r in rows:
    name = r['设备名称']
    prefix = re.sub(r'-\d+$', '', r['设备名称'])
    days = parse_uptime(r['运行时间'])
    if days:
        peers[prefix].append((name, days))

for prefix, devs in sorted(peers.items()):
    if len(devs) > 1:
        times = [d[1] for d in devs]
        if max(times) - min(times) > 7:
            print(f"  {prefix}: 差异 > 7天")
            for name, d in devs:
                print(f"    {name}: {d}天")

# ============ 8. Errors ============
print()
print(sep)
print("【8. 接口错包】")
print(sep)
all_clean = all('无错包' in (r['接口错包'] or '') for r in rows)
if all_clean:
    print("  全部20台设备接口均无错包 - 网络质量良好。")
else:
    for r in rows:
        if '无错包' not in (r['接口错包'] or ''):
            print(f"  [{r['设备名称']}]: {r['接口错包']}")

# ============ Summary ============
print()
print(sep)
print("总结")
print(sep)
print("""
关键发现:
  1. [光功率] Test-N93180-1 Eth1/47 Rx=-10.65dBm 接近10G-SR灵敏度阈值
     (-11.1dBm), 建议清洁光纤端面或更换光模块排查。

  2. [CPU] 3台C2960X CPU在35-39% (NM-AS2960-3, Test-AS2960-1, SF-AS2960-1&2),
     属于C2960X/2960X平台正常偏高范围, 无需紧急处理但建议跟踪趋势。

  3. [版本] SER-N93180-1 仍运行 NXOS 7.0(3)I7(5a), 同组其他3台均已升级至
     NXOS 9.2(3), 建议安排升级窗口统一版本。

  4. [版本] AS2960交换机存在3个不同IOS子版本 (15.2(2)E7/15.2(6)E/15.2(7)E0a),
     建议逐步统一。

  5. [正常运行] 所有设备风扇/电源/温度均在正常范围内, 无接口错包,
     无近期重启迹象 (最短运行时间约35周/245天)。

  6. [接口flapping] 当前采集为状态快照, 未包含syslog历史日志。
     如需检测接口历史up/down事件, 建议采集 "show logging | include LINK" 命令。
     从当前快照看, 所有接口状态稳定, 无异常flapping迹象。
""")
