<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 生产脚本

完整配置组合与流程见 [部署安装](../../docs/10_部署安装手册.md)。脚本默认使用核心 Compose；AI 网关和 AI Worker 需要另行叠加 `docker-compose.ai.yml` 和 `ai` profile。

| 脚本 | 用途 |
| --- | --- |
| `check_env.ps1` / `.sh` | 必填配置和开发密钥预检 |
| `install.ps1` / `.sh` | 安装前准备、拉取和构建镜像 |
| `start.ps1` / `.sh` | core profile 启动、迁移、可选演示账号和冒烟 |
| `stop.ps1` / `.sh` | 停止 Compose；删卷选项仅用于明确销毁数据 |
| `smoke_test.ps1` / `.sh` | BFF、登录、Workflow、权限及前端冒烟 |
| `backup.ps1` / `.sh` | PostgreSQL、MinIO，以及旧 SQLite 环境的可选备份 |

已有正式账号时，启动使用 `-SkipSeed` 或环境变量 `SKIP_SEED=1`。演示初始化命令会重设同名用户密码和分组。冒烟请传入专用账号，不能把默认演示账号作为生产前提。

默认数据库为 PostgreSQL，Django 位于 `django` schema。备份 SQL 已覆盖默认用户与业务数据；只有显式使用旧 SQLite 模式时才需要对应 SQLite 文件。恢复时同时核对 MinIO 对象、数据库引用与摘要。

生产端口使用 `!override` 替换基础列表，仅绑定 loopback。部署前执行完整组合的 `docker compose ... config --quiet`；不支持该标签的 Compose 需要升级。验证完成前不要把脚本成功提示当作真实业务验收。
