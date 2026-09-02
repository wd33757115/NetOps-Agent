# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""获取公文写作结果"""
import sys
import json
import requests
from pathlib import Path

# 添加项目根目录到 PATH
root_dir = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root_dir))
sys.stdout.reconfigure(encoding='utf-8')

# 调用 API
query = '帮我写一份请示，向信息中心申请采购一台新的核心交换机'
r = requests.post(
    'http://localhost:8000/api/v1/chat',
    json={'query': query, 'thread_id': 'doc-test-005'},
    timeout=60
)

data = r.json()
response = data.get('response', '')

# 打印响应内容
print("=" * 80)
print("API 响应内容：")
print("=" * 80)
print(response)
