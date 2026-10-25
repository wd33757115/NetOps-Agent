<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# NetOps Agent

面向网络运维团队的 AIOps 工作台。设备巡检、规则治理、拓扑发现、防火墙策略、资源台账、公文写作和知识问答在同一个工作台里完成；每项业务是一个独立的应用包，按用户授权使用。开放式问答由 DeepSeek Harness SDK（DSH）执行。

## 应用

| 应用 | ID | 用途 |
| --- | --- | --- |
| 网络巡检 | `inspection` | 在线与离线巡检、采集证据、变化与事件、风险整改、巡检报告 |
| 规则治理 | `rule-governance` | 检查项、解析适配器（TextFSM）、规则制作、发布与回退 |
| 设备管理 | `device-management` | 设备资产、分组、连接检测、身份发现、巡检命令集 |
| SSH 终端 | `device-access` | 网页 SSH 终端、会话与主机密钥 |
| 网络拓扑 | `topology` | 基于 LLDP 的拓扑发现、RFC 8345 标准模型、路径查询 |
| NETCONF 接口 | `netconf-api` | 把设备的 NETCONF 表映射成检查项的值 |
| 防火墙策略生成 | `firewall-policy` | 申请表导入、策略计算、配置生成与校验、IP 地址封禁、变更工单 |
| 资源台账 | `resource-ledger` | IP 地址规划、业务系统与主机、运营商专线 |
| 公文写作 | `official-documents` | 公文起草、修订和 Word 导出 |
| 资料管理 | `materials` | 资料导入、修订比较、停用与恢复 |
| 知识问答 | `assistant` | 基于资料和业务证据的问答，入口 `/chat` |
| 能力包接入验证 | `delivery-probe` | 应用包开发的完整示例，默认不启用 |
| 演示应用 | `hello-demo` | 最小的应用包示例，默认不启用 |

应用位于 `src/apps/<目录>/`，由各自的 `app.yaml` 声明前端、API、后台任务和数据库；目录里的 README 说明功能和边界。工作台里的“Skills 和连接器 → 开发指南”介绍如何开发自己的应用、Skill 和 Workflow。

## 架构

浏览器 → React 前端（3000）→ Django BFF（8001，登录与授权）→ FastAPI 网关（8000）→ Celery Worker。PostgreSQL 保存业务数据，Redis 与 RabbitMQ 承担缓存和任务队列，MinIO 保存文件与采集证据；Langfuse 为可选的模型调用追踪。

浏览器只访问前端这一个端口：接口和 WebSocket 由前端转给 BFF，上传文件经 `/object-storage/` 转给 MinIO。其他电脑打开 `http://<服务器地址>:3000` 就能使用全部功能，包括上传。

## 部署

- [Windows 部署](docs/deployment-windows.md)：应用进程运行在本机，中间件运行在 Docker Desktop。
- [Linux 部署](docs/deployment-linux.md)：全部服务运行在 Docker Compose 中。

Windows 上的最短路径（在仓库根目录的 PowerShell 中执行）：

```powershell
.\scripts\windows\install.ps1
.\scripts\windows\bootstrap.ps1 -Action apply
.\scripts\windows\start.ps1 -SkipLangfuse -EnableApps device-management,inspection,rule-governance,device-access,netconf-api,topology,firewall-policy,resource-ledger,official-documents,materials,assistant
```

然后打开 `http://localhost:3000`，用默认管理员 `admin`（密码 `admin123`）登录，登录后到“系统管理 → 用户”重置这个密码；其他电脑访问时的防火墙和主机名设置见部署文档。AI 功能（规则自动制作、公文、问答）需要在 `.env` 中填写 `DEEPSEEK_API_KEY`。

## 许可

[Apache-2.0](LICENSE)
