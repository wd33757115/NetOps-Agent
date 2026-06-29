#!/usr/bin/env python3
"""
混淆巡检数据脚本：
1. 所有 IP 地址统一替换为 192.168.1.1
2. 设备名称替换为 sw-随机数
3. 序列号相关的混淆掉（替换为随机字符串）
"""

import re
import random
import string
import shutil
import sys
from pathlib import Path


def random_serial(length: int = 10) -> str:
    """生成随机序列号（字母+数字）"""
    return "".join(random.choices(string.ascii_uppercase + string.digits, k=length))


def generate_device_name(index: int) -> str:
    """生成混淆后的设备名称"""
    rand_suffix = random.randint(1000, 9999)
    return f"sw-{rand_suffix}"


# ============ 从文件名解析设备名 → 生成替换映射 ============
def build_device_mapping(src_dir: Path) -> dict[str, str]:
    """
    扫描所有文件名，提取设备名称并建立映射。
    设备名 = 去掉最后的 -IP 部分
    """
    mapping = {}
    for f in sorted(src_dir.glob("*.txt")):
        stem = f.stem
        # 文件名格式: XA-FOTIC-Site-DeviceType-Index-IP
        # 设备名通常为去掉最后一个 -数字.数字.数字.数字 部分
        # 但有些文件名包含 &，如 XA-FOTIC-SF-AS2960-1&2-10.34.2.66
        # 用正则去掉末尾的 -IP
        match = re.match(r"^(.+?)-(\d+\.\d+\.\d+\.\d+)$", stem)
        if match:
            device_name = match.group(1)
            mapping[device_name] = None  # 稍后填充
    return mapping


# ============ IP 地址替换 ============
def replace_ip(text: str) -> str:
    """
    将所有合法 IPv4 地址统一替换为 192.168.1.1。
    """
    # 匹配标准 IPv4 地址: 数字.数字.数字.数字
    # 排除版本号模式（包含括号的如 15.2(2)E7）
    # 排除 CIDR 前缀（/数字）但保留 IP 部分
    def _is_version_like(ip_str: str, context: str, start: int, end: int) -> bool:
        """判断是否看起来像版本号而非真实 IP"""
        # 如果前面有 Version、Software 等版本关键字
        before = context[max(0, start - 30):start]
        if re.search(r'(?:Version|Software|SW\s+Version|IOS|NXOS|BIOS)\b\s*:?\s*$', before, re.IGNORECASE):
            return True
        # 检查 IP 后是否紧跟括号
        after = context[end:end + 15]
        if re.match(r'^\(\d+\)', after):
            return True
        return False

    def _replacer(m: re.Match) -> str:
        ip = m.group(0)
        start = m.start()
        end_ = m.end()
        full = text
        # 跳过版本号场景
        if _is_version_like(ip, full, start, end_):
            return ip
        # 跳过 0.0.0.0（默认路由）
        if ip == "0.0.0.0":
            return ip
        # 跳过子网掩码（如 255.255.255.0）
        if ip.startswith("255."):
            return ip
        # 跳过 127.x.x.x 回环地址
        if ip.startswith("127."):
            return ip
        return "192.168.1.1"

    # 匹配独立 IP 地址（不在更大数字中的）
    pattern = r'\b(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\b'
    return re.sub(pattern, _replacer, text)


def replace_cidr_ip(text: str) -> str:
    """
    替换 CIDR 格式 IP（如 10.34.2.2/32 → 192.168.1.1/32）。
    注意：必须在普通 IP 替换之前调用。
    """
    pattern = r'\b(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)\.(25[0-5]|2[0-4][0-9]|[01]?[0-9][0-9]?)/(\d+)\b'

    def _replacer(m: re.Match) -> str:
        ip = f"{m.group(1)}.{m.group(2)}.{m.group(3)}.{m.group(4)}"
        mask = m.group(5)
        # 跳过 0.0.0.0
        if ip == "0.0.0.0":
            return m.group(0)
        # 跳过 255.x.x.x 子网掩码
        if ip.startswith("255."):
            return m.group(0)
        return f"192.168.1.1/{mask}"

    return re.sub(pattern, _replacer, text)


# ============ 序列号混淆 ============
def obfuscate_serial(text: str) -> str:
    """
    混淆序列号。
    常见格式：
    - FDO22350PHA (11位字母数字)
    - FGL224630K5 (11位)
    - DCC2238L31Z (11位)
    - LIT22383XH2 (11位)
    - FOC2222S1M4 (11位)
    - ACW23180C1Z (11位)
    - QCS23114HJF (11位)
    - OPM21170MMP (11位)
    - AVP2325K15K-B (带后缀)
    - FIW25050480-A (带后缀)
    以及 "SN: xxx", "Serial#", "serial number: xxx" 等标签后的值
    """
    serial_store = {}  # 同一序列号用同一混淆值

    # 匹配标签 + 序列号的模式
    def _tag_replacer(m: re.Match) -> str:
        tag = m.group(1)
        value = m.group(2)
        if value not in serial_store:
            # 保持大致长度一致
            serial_store[value] = random_serial(len(value))
        return f"{tag}{serial_store[value]}"

    # SN: FDO22350PHA 格式
    text = re.sub(
        r'(SN:\s*)([A-Z0-9]{8,15}(?:-[A-Z0-9]{1,4})?)',
        _tag_replacer,
        text,
    )

    # Serial# 格式（表格中）
    def _serial_hash_replacer(m: re.Match) -> str:
        value = m.group(1)
        if value not in serial_store:
            serial_store[value] = random_serial(len(value))
        return serial_store[value]

    # serial number is xxx 格式
    text = re.sub(
        r'(serial\s+number\s+is\s+)([A-Z0-9]{8,15}(?:-[A-Z0-9]{1,4})?)',
        _tag_replacer,
        text,
        flags=re.IGNORECASE,
    )

    # serial number : xxx 格式（多空格）
    text = re.sub(
        r'((?:Motherboard|Power\s*supply|Daughterboard|System)\s+serial\s+number\s+:\s+)([A-Z0-9]{8,15})',
        _tag_replacer,
        text,
        flags=re.IGNORECASE,
    )

    # Processor Board ID 序列号
    text = re.sub(
        r'((?:Processor\s+[Bb]oard\s+ID)\s+)([A-Z0-9]{8,15})',
        _tag_replacer,
        text,
        flags=re.IGNORECASE,
    )

    return text


# ============ 设备名称混淆 ============
def obfuscate_device_names(text: str, name_mapping: dict[str, str]) -> str:
    """
    将文本中的设备名称替换为映射后的名称。
    """
    # 按名称长度降序排列，避免短名称先匹配（如 XA-FOTIC-SER-N93180-1 和 XA-FOTIC-SER-N93180-3）
    sorted_names = sorted(name_mapping.keys(), key=len, reverse=True)

    for original in sorted_names:
        replacement = name_mapping[original]
        if original and replacement and original != replacement:
            text = text.replace(original, replacement)

    return text


# ============ 主流程 ============
def main():
    src_dir = Path(r"C:\Users\wangd\PycharmProjects\PythonProject\netops-agent\TEXTFSMTEST")
    out_dir = src_dir / "obfuscated"
    out_dir.mkdir(exist_ok=True)

    # 构建设备名映射
    raw_mapping = build_device_mapping(src_dir)
    device_mapping: dict[str, str] = {}
    for idx, name in enumerate(sorted(raw_mapping.keys()), start=1):
        device_mapping[name] = generate_device_name(idx)

    print("设备名称映射:")
    for orig, new in device_mapping.items():
        print(f"  {orig} → {new}")

    print(f"\n处理文件 ({len(list(src_dir.glob('*.txt')))} 个):")

    for txt_file in sorted(src_dir.glob("*.txt")):
        print(f"  处理: {txt_file.name}")

        # 读取文件
        try:
            with open(txt_file, "r", encoding="utf-8") as f:
                content = f.read()
        except UnicodeDecodeError:
            with open(txt_file, "r", encoding="gb18030") as f:
                content = f.read()

        # 1. 先替换 CIDR 格式 IP
        content = replace_cidr_ip(content)

        # 2. 替换普通 IP 地址
        content = replace_ip(content)

        # 3. 混淆序列号
        content = obfuscate_serial(content)

        # 4. 替换设备名称
        content = obfuscate_device_names(content, device_mapping)

        # 生成新文件名（设备名和 IP 都已混淆）
        stem = txt_file.stem
        # 替换文件名中的设备名
        new_stem = stem
        for orig, new in device_mapping.items():
            new_stem = new_stem.replace(orig, new)
        # 替换文件名中的 IP
        new_stem = replace_ip(new_stem)
        new_filename = new_stem + ".txt"

        out_path = out_dir / new_filename
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(content)

        print(f"    → {new_filename}")

    print(f"\n完成！混淆后文件保存在: {out_dir}")


if __name__ == "__main__":
    main()
