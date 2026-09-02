# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""测试 API 返回的下载链接"""
import sys
import requests
import json
from pathlib import Path

# 添加项目根目录到 PATH
root_dir = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root_dir))
sys.stdout.reconfigure(encoding='utf-8')

# 调用 API
r = requests.post(
    'http://localhost:8000/api/v1/chat',
    json={'query': '帮我写一份请示', 'thread_id': 'docx-api-test'},
    timeout=90
)

data = r.json()

print("=" * 80)
print("API 响应:")
print("=" * 80)

# 检查 download_url 是否在响应中
response_text = data.get('response', '')

if 'Download URL' in response_text:
    print("✅ 下载链接已包含在响应中")
    # 提取下载链接
    for line in response_text.split('\n'):
        if 'Download URL' in line:
            print(line)
elif 'download_url' in response_text:
    print("✅ download_url 已包含在响应中")
    print(response_text)
else:
    print("❌ 响应中未找到下载链接")
    print("\n响应内容预览:")
    print(response_text[:1000])

print("\n" + "=" * 80)
