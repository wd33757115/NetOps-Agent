<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 脚本目录

所有命令默认从仓库根目录执行。完整步骤见 [开发与构建](../docs/06_环境%20&%20编译构建说明.md)、[部署](../docs/10_部署安装手册.md)和[维护与验证](../docs/maintenance.md)。

| 目录 / 文件 | 用途 |
| --- | --- |
| `test/install.ps1` | Windows 虚拟环境、Python 依赖、锁定的 npm 依赖和中间件镜像 |
| `test/start.ps1` / `test/stop.ps1` | 本地中间件、核心/AI Worker、Beat、API、BFF 和前端 |
| `prod/` | Compose 预检、启动、停止、冒烟和备份 |
| `assistant/` | 本地资料提取、DSH 执行器与工具验证 |
| `migrate_db.py` | Alembic 业务 schema 升级；`--check` 只验证版本 |
| `init_db.py` | 业务 schema 初始化 |
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

启动前先检查 App 锁定依赖（包含 DSH SDK/runtime），缺失时在停止现有服务前退出。
仅检查、不重启可运行 `.\scripts\test\start.ps1 -CheckOnly`。
升级依赖可运行 `.\scripts\test\install.ps1 -SkipDocker -SkipNpm`，安装时合并各 App 的 requirements.lock。
首次启用知识问答使用 `.\scripts\test\start.ps1 -SkipLangfuse -EnableApps assistant`；已有启用状态与用户授权继续保留。

默认前端是构建预览，热更新加 `-DevMode`。已有中间件时加 `-SkipMiddleware`；停止时可加 `-KeepMiddleware` / `-KeepLangfuse`。日志与进程信息位于 `.runtime/test/`。

### App 启动

在 `.env` 中设置至少 32 字符的 `NETOPS_APP_INTERNAL_SECRET`，供 BFF、Gateway、Worker 和 Beat
使用同一内部授权凭据。启动脚本会统一本地授权地址（含自定义 Django 端口）和 Broker/结果存储配置，
执行迁移，再安装新发现的包或更新未启用包的摘要。不会修改用户授权，也不会自动启用已停用的 App。

首次启用四个业务 App：

```powershell
.\scripts\test\start.ps1 -SkipLangfuse -EnableApps official-documents,firewall-policy,rule-governance,inspection
```

以后按原命令启动即可，已启用状态会保留。单独启用巡检时只传 `-EnableApps inspection`。
启用及启动验证检查真实 Gateway、生产前端文件、Worker 和 Beat 的摘要与任务注册；检查失败会退出，
不会输出“环境就绪”。`-NoBrowser` 可关闭自动打开页面，后台进程使用隐藏窗口，日志仍写入 `.runtime/test/logs/`。

核心 Worker 消费 `netops.patrol` 等原有基础队列，AI Worker 自动发现业务 App 队列，并排除
已由核心 Worker 消费的巡检队列；保留一个 Beat。无需在启动脚本逐个追加新 App 队列。

正在启用的包若源码摘要已变化，需先用旧部署执行 `app_packages disable APP_ID` 并完成 drain，
再升级启动。脚本不会替代排空或擅自恢复启用。`-DevMode` 仅用于热更新开发，不能与 `-EnableApps`
组合，也不作为生产 App 部署验收；正式启用使用默认构建预览。

原始巡检的规则/设备业务就绪检查仍在巡检启动入口执行；环境冒烟不再要求停用的巡检 App
能够发起业务。检查所有已启用包也可单独运行 `scripts/test/verify.ps1`（端口参数与启动保持一致）。

问答统一使用 DSH 执行器，由 Gateway 按请求启动和回收 SDK；不启动单独 Agent 服务。LangGraph/Supervisor 启动选项已退役。

### 助手资料与执行器验证

`assistant/prepare_docx_sample.py` 仅离线提取指定 DOCX，在 `.runtime/assistant-eval/` 保存文本、
摘要和段落/表格位置，不修改原文件、不加入共享知识库、不调用模型。`--prepare-only` 可只提取；
项目 Python 可再调用该模块的 `verify(output_directory)` 校验章节分页和引用。
图片、自动编号及合并单元格等完整性限制会记录在 manifest 中。

用户已允许华为云 Stack 样本的少量片段验证，禁止整份发送模型。DSH 官方 SDK/MCP 隔离试验见 [助手脚本说明](assistant/README.md)，范围和实测结果见 [P6 实施记录](../docs/netops-capability-packages-p6.md)。
原知识库及 `KNOWLEDGE_TOOL_QA_ENABLED` 桥接开关已删除；正式问答与旧 REST/SSE 聊天已统一使用 DSH。

## 测试与生产

完整测试应使用独立测试数据库、队列和对象存储。`test/` 下的业务回放脚本可能创建样本、任务或资产，文件位于测试目录不代表没有数据副作用。

生产脚本默认使用 core profile；完整业务 App 和 AI Worker 使用 `-WithAI` / `WITH_AI=1` 自动叠加 `deployment/docker-compose.ai.yml`。已有正式账号时使用 `start.ps1 -SkipSeed` / `SKIP_SEED=1`，避免演示用户命令重设账号。具体限制及流程见 [生产脚本说明](prod/README.md)。
