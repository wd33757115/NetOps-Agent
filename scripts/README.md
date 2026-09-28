<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 脚本目录

所有命令默认从仓库根目录执行。完整步骤见 [开发与构建](../docs/06_环境%20&%20编译构建说明.md)、[部署](../docs/10_部署安装手册.md)和[维护与验证](../docs/maintenance.md)。

| 目录 / 文件 | 用途 |
| --- | --- |
| `windows/install.ps1` | Windows 虚拟环境、Python 依赖、锁定的 npm 依赖和中间件镜像 |
| `windows/start.ps1` / `windows/stop.ps1` | 本地中间件、核心/AI Worker、Beat、API、BFF 和前端 |
| `windows/` | Windows 唯一部署入口，含 prerequisites、containers、verify、backup；见[指南](../docs/windows-deployment.md) |
| `prod/` | Linux Compose 预检、启动、停止、冒烟和备份 |
| `assistant/` | 本地资料提取、DSH 执行器与工具验证 |
| `check_items/cisco_manual_batch.py` | Cisco 样本本地脱敏、排除空输出与手工 TextFSM 回放；不写库、不调用生成器 |
| `check_items/h3c_manual_batch.py` | H3C 样本本地脱敏、识别文件编码、去重、排除空输出与手工 TextFSM 回放；不写库、不调用生成器 |
| `windows/bootstrap.ps1` | 平台数据库统一初始化/升级与只读就绪检查 |
| `migrate_db.py` | 兼容转发至平台 bootstrap；`--check` 转发只读检查 |
| `init_db.py` | 兼容转发至平台 bootstrap，不包含独立初始化逻辑 |
| `migrate_sqlite_data.py` / `migrate_django_sqlite.py` | 历史 SQLite 数据迁入；保留用于旧部署升级 |
| `docs/audit_docs.py` | 文档登记、过期状态和链接检查 |
| `deprecations/` | 历史路径调用观测及退出证据 |
| `validate_ps1.ps1` | PowerShell 语法检查，不运行被检查脚本 |
| `test/` 中的 Python 工具 | 样本回放、业务诊断与集成验收；先阅读参数和数据副作用 |

## 本地联调

```powershell
.\scripts\windows\install.ps1
.\scripts\windows\start.ps1 -SkipLangfuse
```

启动前先检查 App 锁定依赖（包含 DSH SDK/runtime），缺失时在停止现有服务前退出。
仅检查、不重启可运行 `.\scripts\windows\start.ps1 -CheckOnly`。
升级依赖可运行 `.\scripts\windows\install.ps1 -SkipDocker -SkipNpm`，安装时合并各 App 的 requirements.lock。
首次启用知识问答使用 `.\scripts\windows\start.ps1 -SkipLangfuse -EnableApps assistant`；已有启用状态与用户授权继续保留。

默认前端是构建预览，热更新加 `-DevMode`。已有中间件时加 `-SkipMiddleware`；停止时可加 `-KeepMiddleware` / `-KeepLangfuse`。日志与进程信息位于 `.runtime/test/`。

### App 启动

在 `.env` 中设置至少 32 字符的 `NETOPS_APP_INTERNAL_SECRET`，供 BFF、Gateway、Worker 和 Beat
使用同一内部授权凭据。启动脚本会统一本地授权地址（含自定义 Django 端口）和 Broker/结果存储配置，
只读检查平台数据库与 App 登记是否就绪；安装或升级先执行 `windows/bootstrap.ps1 -Action apply`。不会修改用户授权，也不会自动启用已停用的 App。

首次启用四个业务 App：

```powershell
.\scripts\windows\start.ps1 -SkipLangfuse -EnableApps official-documents,firewall-policy,rule-governance,inspection
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
能够发起业务。检查所有已启用包也可单独运行 `scripts/windows/verify.ps1`（端口参数与启动保持一致）。

问答统一使用 DSH 执行器，由 Gateway 按请求启动和回收 SDK；不启动单独 Agent 服务。LangGraph/Supervisor 启动选项已退役。

### 助手资料与执行器验证

`assistant/prepare_docx_sample.py` 仅离线提取指定 DOCX，在 `.runtime/assistant-eval/` 保存文本、
摘要和段落/表格位置，不修改原文件、不加入共享知识库、不调用模型。`--prepare-only` 可只提取；
项目 Python 可再调用该模块的 `verify(output_directory)` 校验章节分页和引用。
图片、自动编号及合并单元格等完整性限制会记录在 manifest 中。

用户已允许华为云 Stack 样本的少量片段验证，禁止整份发送模型。DSH 官方 SDK/MCP 隔离试验见 [助手脚本说明](assistant/README.md)；当前使用方式见[问答指南](../docs/assistant-guide.md)。
原知识库及 `KNOWLEDGE_TOOL_QA_ENABLED` 桥接开关已删除；正式问答与旧 REST/SSE 聊天已统一使用 DSH。

## 测试与生产

检查项语义变化后的规则重建使用 `check_items/refresh_semantic_rules.py`。
`plan` 只读生成候选并验证执行器行为；`apply` 会停用旧规则、发布新规则，依赖运行包需要重新装配。
操作边界、当前结果与命令见[规则重建记录](../docs/check-item-semantic-rule-refresh.md)。

完整测试应使用独立测试数据库、队列和对象存储。`test/` 下的业务回放脚本可能创建样本、任务或资产，文件位于测试目录不代表没有数据副作用。

Linux 生产脚本默认使用 core profile；完整业务 App 和 AI Worker 使用 `WITH_AI=1` 自动叠加 `deployment/docker-compose.ai.yml`。启动不再创建演示账号；数据库初始化由统一 bootstrap 作业完成。具体限制及流程见 [生产脚本说明](prod/README.md)。

性能回放与读取压测：`scripts/test/team_capacity.py`，操作与边界见 `docs/team-capacity-plan.md`。
