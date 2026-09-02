# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

"""测试 MinIO 上传"""
import sys
from pathlib import Path

# 添加项目根目录到 PATH
root_dir = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(root_dir))
sys.stdout.reconfigure(encoding='utf-8')

from src.infrastructure.storage.minio_client import get_minio_storage

# 测试 MinIO 连接
minio = get_minio_storage()
print(f"MinIO ready: {minio.is_ready()}")

if minio.is_ready():
    # 测试上传
    test_content = b"Test content"
    filename = "test.txt"

    success = minio.upload_file(
        object_name=f"documents/test_{filename}",
        file_data=test_content,
        content_type="text/plain"
    )

    print(f"Upload success: {success}")

    if success:
        url = minio.get_presigned_url(f"documents/test_{filename}", expires=3600)
        print(f"Download URL: {url}")
else:
    print("MinIO not ready, skipping test")
