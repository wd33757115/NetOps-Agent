<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 脚本目录

所有命令默认从仓库根目录执行。完整步骤见 [开发与构建](../docs/06_环境%20&%20编译构建说明.md)、[部署](../docs/10_部署安装手册.md)和[维护与验证](../docs/maintenance.md)。

| 目录 / 文件 | 用途 |
| --- | --- |
| `test/install.ps1` | Windows 虚拟环境、Python 依赖、锁定的 npm 依赖和中间件镜像 |
| `test/start.ps1` / `test/stop.ps1` | 本地中间件、核心/AI Worker、Beat、API、BFF 和前端 |
| `prod/` | Compose 预检、启动、停止、冒烟和备份 |
| `migrate_db.py` | Alembic 业务 schema 升级；`--check` 只验证版本 |
| `init_db.py` | 业务 schema 与 LangGraph checkpoint 初始化 |
| `migrate_sqlite_data.py` / `migrate_django_sqlite.py` | 历史 SQLite 数据迁入；保留用于旧部署升级 |
| `docs/audit_docs.py` | 文档登记、过期状态和链接检查 |
| `deprecations/` | 历史路径调用观测及退出证据 |
| `validate_ps1.ps1` | PowerShell 语法检查，不运行被检查脚本 |
| `test/` 中的 Python 工具 | 样本回放、业务诊断与集成验收；先阅读参数和数据副作用 |

## 本地联调

```powershell
.\scripts\test\install.ps1
.\scripts\test\start.ps1 -SkipLangfuse
```

默认前端是构建预览，热更新加 `-DevMode`。已有中间件时加 `-SkipMiddleware`；停止时可加 `-KeepMiddleware` / `-KeepLangfuse`。日志与进程信息位于 `.runtime/test/`。

Supervisor 入口固定为 v2，不再提供 v1 启动选项。不要通过旧文档中的 `USE_SUPERVISOR_V2` 变量判断当前执行方式。

## 测试与生产

完整测试应使用独立测试数据库、队列和对象存储。`test/` 下的业务回放脚本可能创建样本、任务或资产，文件位于测试目录不代表没有数据副作用。

生产脚本默认使用 core profile；AI 需要叠加 `deployment/docker-compose.ai.yml`。已有正式账号时使用 `start.ps1 -SkipSeed` / `SKIP_SEED=1`，避免演示用户命令重设账号。具体限制及流程见 [生产脚本说明](prod/README.md)。
