<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps Agent

面向数据中心网络的 AIOps 工作台。巡检、规则治理、防火墙策略、公文写作和资料管理以独立业务包交付；开放式问答由 DeepSeek Harness SDK（DSH）执行。

## 应用入口

| 应用 | 路径 | 用途 |
|---|---|---|
| 工作台 | `/workbench` | 个人应用布局、文件夹、Dock、桌面壁纸及会话入口 |
| 网络巡检 | `/apps/inspection` | 在线/离线巡检、证据、风险整改及报告 |
| 规则治理 | `/apps/rule-governance` | 检查项、解析适配器、规则制作与发布 |
| 设备管理 | `/apps/device-management` | 设备、凭据与巡检命令集 |
| 防火墙策略 | `/apps/firewall-policy` | 申请导入、策略核对、配置及工单导出 |
| 公文写作 | `/apps/official-documents` | 起草、修订和 Word 导出 |
| 资料管理 | `/apps/materials` | 整份资料导入发布、修订比较、停用/恢复/删除 |
| 知识问答 | `/chat` | 按需搜索、阅读资料，查询巡检业务证据；旧 `/apps/assistant` 地址跳转到聊天页 |

业务 App 通过 `app.yaml` 自注册前端、API、工具与任务，按用户/用户组授予 App 使用权。巡检和规则治理使用固定业务流程与 Celery，新任务不依赖 Workflow。DSH 是问答的唯一 Agent 执行器；原 LangGraph/Supervisor 和 RAG 知识库入口已退出。

## 本地启动

Python 3.11、Node.js 22、Docker Compose v2。从项目根目录执行；已有 `.env` 时不要覆盖。

```powershell
Copy-Item .env.example .env
.\scripts\test\install.ps1
.\scripts\test\start.ps1 -EnableApps official-documents,firewall-policy,rule-governance,inspection,materials,assistant
```

后续直接运行 `scripts/test/start.ps1` 保留启用状态与授权。默认构建预览前端，热更新使用 `-DevMode`；只做启动预检使用 `-CheckOnly`。应用源码升级必须先完成停用排空，见[能力包部署](docs/capability-packages-guide.md)。

React 默认端口 3000，Django BFF 8001，FastAPI 8000。日志在 `.runtime/test/logs/`；停止使用 `scripts/test/stop.ps1`。浏览器经 BFF 访问业务 API，PostgreSQL、Redis、RabbitMQ、MinIO 提供持久化与任务基础设施，Langfuse 为可选观测服务。

## 文档

[文档中心](docs/README.md) · [系统架构](docs/01_系统架构设计.md) · [工作台](docs/workbench-apps-guide-2026-09-05.md) · [开发与构建](docs/06_环境%20&%20编译构建说明.md) · [实施状态与待验收项](docs/netops-architecture-implementation-plan.md)

代码测试和本地部署通过不等于真实设备、专家问答或生产服务器验收完成。当前验收边界见对应文档。

## 许可

[Apache-2.0](LICENSE)

系统管理：管理员从工作台顶部进入 `/system/users`，统一维护用户、业务用户组与应用授权。参见[操作说明](docs/system-management-guide.md)和[实施计划](docs/system-management-plan.md)。

规则包迁移：规则治理内支持检查项、TextFSM、Change 和状态/事件规则的依赖导出、差异预检、草稿导入与记录追溯。参见[使用说明](docs/rule-package-transfer-guide.md)和[实施计划](docs/rule-package-transfer-plan.md)。

规则治理 1.4.0 已删除独立数据契约和旧生成链，当前入口及迁移边界见[规则资产治理](docs/rule-asset-control-plane-guide.md)。
