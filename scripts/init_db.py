#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com>
# SPDX-License-Identifier: Apache-2.0

import sys
from pathlib import Path

BASE_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(BASE_DIR))

from src.infrastructure.db.migrations import upgrade_database
from src.infrastructure.db.postgres import init_postgres_schema, verify_postgres_connection

if __name__ == "__main__":
    print("=" * 60)
    print("🚀 NetOps Multi-Agent 数据库初始化")
    print("=" * 60)

    print("\n📡 验证 PostgreSQL 连接...")
    if verify_postgres_connection():
        print("✅ PostgreSQL 连接成功!")
    else:
        print("❌ PostgreSQL 连接失败，请检查配置")
        sys.exit(1)

    print("\n🏗️  创建 LangGraph Checkpoint 表...")
    init_postgres_schema()

    print("\n📊 创建业务状态表...")
    upgrade_database()

    print("\n" + "=" * 60)
    print("✅ 数据库初始化全部完成!")
    print("=" * 60)
