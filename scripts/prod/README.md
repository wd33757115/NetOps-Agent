<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 生产脚本

完整配置组合与流程见 [部署安装](../../docs/10_部署安装手册.md)。脚本默认使用核心 Compose；
传 `-WithAI` / `WITH_AI=1` 自动叠加 `docker-compose.ai.yml` 和 `ai` profile，启动完整业务 App 队列消费者及 AI 网关。

| 脚本 | 用途 |
| --- | --- |
| `check_env.ps1` / `.sh` | 必填配置和开发密钥预检 |
| `install.ps1` / `.sh` | 安装前准备、拉取和构建镜像 |
| `start.ps1` / `.sh` | core profile 启动、迁移、可选演示账号和冒烟 |
| `stop.ps1` / `.sh` | 停止 Compose；删卷选项仅用于明确销毁数据 |
| `smoke_test.ps1` / `.sh` | BFF、登录、Workflow、权限及前端冒烟 |
| `backup.ps1` / `.sh` | PostgreSQL、MinIO，以及旧 SQLite 环境的可选备份 |

已有正式账号时，启动使用 `-SkipSeed` 或环境变量 `SKIP_SEED=1`。演示初始化命令会重设同名用户密码和分组。冒烟请传入专用账号，不能把默认演示账号作为生产前提。

## 应用包部署

`.env` 必须配置至少 32 字符的 `NETOPS_APP_INTERNAL_SECRET`。各容器使用同一凭据；
Django 的部署探测与 Worker 使用相同 Broker/结果库，内部授权地址 `django:8001` 已加入允许的主机名。

```powershell
.\scripts\prod\start.ps1 -WithAI -SkipSeed -EnableApps official-documents,firewall-policy,rule-governance,inspection
```

```bash
WITH_AI=1 SKIP_SEED=1 ENABLE_APPS=official-documents,firewall-policy,rule-governance,inspection bash scripts/prod/start.sh
```

`ENABLE_APPS` 使用逗号分隔。日常重启可省略启用参数，但已启用完整业务 App 时继续保留 `-WithAI` / `WITH_AI=1`。
停止脚本会同时覆盖 core 和 AI 服务，避免遗留队列消费者或 Beat；升级前的 App 排空仍需先完成。
脚本按迁移 → `app_packages prepare` → 已启用包 verify → 显式 enable → 冒烟执行。
新包安装后默认停用；未启用包可更新摘要；已启用但代码变化的包会拒绝升级，需在替换代码前
使用旧部署 disable/drain。用户和用户组 AppGrant 不由启动脚本新增或撤销。

迁移失败、包安装失败、Gateway/前端/Worker/Beat 摘要不一致或缺少队列消费者都会使启动失败。
部署探测使用容器内 `http://fastapi:8000` 与 `http://react`，不要求暴露 Gateway 的宿主端口。
`-SkipSmoke` 只跳过原有 HTTP 冒烟，不跳过 App 部署检查。巡检业务就绪由启动巡检时的门禁判断，
不能把环境冒烟当作真实设备巡检成功。

默认数据库为 PostgreSQL，Django 位于 `django` schema。备份 SQL 已覆盖默认用户与业务数据；只有显式使用旧 SQLite 模式时才需要对应 SQLite 文件。恢复时同时核对 MinIO 对象、数据库引用与摘要。

生产端口使用 `!override` 替换基础列表，仅绑定 loopback。部署前执行完整组合的 `docker compose ... config --quiet`；不支持该标签的 Compose 需要升级。验证完成前不要把脚本成功提示当作真实业务验收。

## DSH 知识问答

DSH SDK/runtime 由 App 锁文件随镜像安装，启动脚本会检查 Gateway 与 Django 中的锁定版本。
缺失或版本不一致时停止后续部署步骤，需重新构建镜像，不能用 SkipBuild 继续启动旧镜像。
DSH 在 Gateway 中按请求运行，无需添加独立 Agent 容器；业务 AI Worker 仍按原 WithAI 选项部署。
首次启用问答：PowerShell 加 `-EnableApps assistant`，Bash 使用 `ENABLE_APPS=assistant`。
日后原命令保留启用状态；脚本不新增用户授权，也不在启动时运行资料提取或模型验证。
工具目录为 `scripts/assistant/`，不再按实施阶段命名。
