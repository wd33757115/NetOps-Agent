
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""
配置对比工具脚本
"""
import difflib


def diff_config(config1: str, config2: str) -> dict:
    """
    对比两个配置

    Args:
        config1: 配置1
        config2: 配置2

    Returns:
        对比结果
    """
    lines1 = config1.strip().split('\n')
    lines2 = config2.strip().split('\n')

    diff = difflib.unified_diff(lines1, lines2, lineterm='')

    added = []
    removed = []

    for line in diff:
        if line.startswith('+') and not line.startswith('+++'):
            added.append(line[1:])
        elif line.startswith('-') and not line.startswith('---'):
            removed.append(line[1:])

    return {
        'added': added,
        'removed': removed,
        'added_count': len(added),
        'removed_count': len(removed),
        'total_changes': len(added) + len(removed)
    }


def format_diff_result(result: dict) -> str:
    """
    格式化对比结果

    Args:
        result: 对比结果

    Returns:
        格式化后的字符串
    """
    output = []
    output.append("=== 配置对比结果 ===")
    output.append(f"新增: {result['added_count']} 行")
    output.append(f"删除: {result['removed_count']} 行")
    output.append(f"总计: {result['total_changes']} 行变更")
    output.append("")

    if result['added']:
        output.append("=== 新增配置 ===")
        output.extend([f"+ {line}" for line in result['added']])
        output.append("")

    if result['removed']:
        output.append("=== 删除配置 ===")
        output.extend([f"- {line}" for line in result['removed']])
        output.append("")

    return '\n'.join(output)


if __name__ == '__main__':
    # 简单测试
    config_a = """interface GigabitEthernet0/1
 ip address 192.168.1.1 255.255.255.0
!
interface GigabitEthernet0/2
 ip address 10.0.0.1 255.255.255.0
"""

    config_b = """interface GigabitEthernet0/1
 ip address 192.168.1.1 255.255.255.0
!
interface GigabitEthernet0/2
 ip address 10.0.0.1 255.255.255.0
!
interface GigabitEthernet0/3
 ip address 172.16.0.1 255.255.255.0
"""

    result = diff_config(config_a, config_b)
    print(format_diff_result(result))
