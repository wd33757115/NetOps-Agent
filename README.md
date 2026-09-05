<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps Agent

NetOps Agent 提供网络巡检、巡检规则制作、公文写作和 AI 对话。业务应用从工作台进入；Skill 和 Workflow 承担底层执行与长流程编排。

## 当前入口

| 功能 | 路径 | 用途 |
| --- | --- | --- |
| 工作台 | `/workbench` | 应用图标、文件夹、Dock 和个人布局 |
| 网络巡检 | `/apps/patrol` | 在线采集、离线分析、任务与报告 |
| 巡检规则工作台 | `/apps/patrol-rules` | 人工定义检查项及命令关联，AI 制作、验证和授权发布 |
| 设备配置 | `/admin/netops/devices` | 设备、凭据与巡检方案配置 |
| 公文写作 | `/apps/official-documents` | 全文起草、修改、版本管理和 Word 导出 |
| 对话 | `/chat` | Supervisor v2 调度 Skill、Workflow 与知识问答 |

设备配置和规则制作共用 `netops.configure` 权限。前端控制入口，Django BFF 与 FastAPI 执行权限校验；管理员可以授权操作员配置，自动发布另需管理员授权。

## 架构

```text
浏览器 React → Django BFF → FastAPI 业务 API
                              ├─ 巡检、规则资产、公文、设备、存储
                              ├─ Supervisor v2 / Skill / Workflow
                              └─ Celery Worker + Beat
                                  ├─ netops.patrol
                                  ├─ netops.default / firewall / health
                                  └─ netops.ai
数据：PostgreSQL、Redis、RabbitMQ、MinIO
可选：RAG / Chroma、Qdrant、Langfuse
```

Django 默认使用 PostgreSQL 的 `django` schema，业务数据由 SQLAlchemy/Alembic 管理。数据库迁移与运行进程分开执行。巡检运行包固定资产版本；运行时不能因为缺失规则而静默调用 AI 补齐或把未知当作正常。

## 本地开发

需要 Python 3.11、Node.js 22 和 Docker Compose v2。从项目根目录执行；首次创建 `.env` 后按实际环境修改数据库、对象存储、认证和模型配置。

```powershell
Copy-Item .env.example .env
.\scripts\test\install.ps1
.\scripts\test\start.ps1 -SkipLangfuse
```

已有 `.env` 时跳过复制。默认前端为 `http://localhost:3000`，BFF 为 `http://localhost:8001`，FastAPI 本地端口为 `8000`。测试启动脚本默认构建并预览前端；开发热更新使用 `-DevMode`。停止命令为 `.\scripts\test\stop.ps1`，日志位于 `.runtime/test/logs/`。

完整安装、迁移、独立进程和构建步骤见 [开发与构建](docs/06_环境%20&%20编译构建说明.md)。默认核心 Compose 不包含 AI Worker；需要规则生成和公文任务时叠加 `deployment/docker-compose.ai.yml`，见 [部署说明](docs/10_部署安装手册.md)。

## 文档与维护

- [文档中心](docs/README.md)：按功能与开发职责查找当前说明。
- [系统架构](docs/01_系统架构设计.md)：模块职责、数据流和权限边界。
- [维护与验证](docs/maintenance.md)：回归命令、文档检查和旧代码退出方法。
- [脚本目录](scripts/README.md)：开发、生产、迁移和诊断脚本。

旧阶段方案和一次性验收快照通过 Git 历史查阅，不作为当前运行状态的证据。历史兼容读取、数据库迁移与持久任务不能仅因名称含有 legacy 就删除。

## 许可

[Apache-2.0](LICENSE)
