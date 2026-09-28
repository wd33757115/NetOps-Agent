# Windows 安装与部署

核对日期：2026-09-27。本机应用进程配合 Docker Desktop 中间件，统一入口为 `scripts/windows/`。
Windows 容器只使用 `deployment/compose.windows.yml`，无需拼接多个 Compose 文件。
Linux 的角色镜像与 Compose 保留；其迁移任务同样调用平台 bootstrap，完整 Linux 部署仍需独立验收。

## 首次安装

已验证基线为 Windows x64、Python 3.13、Node.js 22 及以上、Docker Desktop 的 Linux containers / WSL 2 后端。
如未安装基础软件，在 PowerShell 执行：

```powershell
.\scripts\windows\prerequisites.ps1
```

此脚本通过 WinGet 安装缺少的 Python、Node.js LTS、Docker Desktop，保留已有安装。
安装器要求提权或重启时按提示操作；完成后重新打开 PowerShell，启动 Docker Desktop 并等待引擎就绪。
WSL 2 和虚拟化要求见 [Docker 官方 Windows 安装说明](https://docs.docker.com/desktop/setup/install/windows-install/)；
软件安装方式见 [Microsoft WinGet 文档](https://learn.microsoft.com/windows/package-manager/winget/install)。
已有 Python 不是 3.13 时，将安装脚本的 `-PythonCommand` 指向 Python 3.13 的完整路径。

从仓库根目录执行：

```powershell
.\scripts\windows\install.ps1
# 首次自动生成 .env；填写配置后统一初始化数据库
.\scripts\windows\bootstrap.ps1 -Action apply
.\scripts\windows\start.ps1 -SkipLangfuse -EnableApps device-management,inspection,rule-governance,firewall-policy,official-documents,materials,assistant
.\venv\Scripts\python.exe web\django_backend\manage.py createsuperuser
```

安装包括 venv、Python/App 依赖、`npm ci`、容器镜像拉取及中间件健康检查。
`.env` 不存在时从模板生成认证密钥、内部调用密钥和数据库/MinIO 密码；已有文件原样保留，不自动更换密码。
不要在全新安装前直接复制含 `change-me` 的模板。已手工创建的配置须自行补齐密钥。
安装不会创建演示账号、修改用户授权或启用 App；启用由上面的显式 `-EnableApps` 完成。
之后在系统管理中创建用户、用户组及应用授权，见[应用操作指南](app-operations-guide.md)。

默认 pip 索引是 HTTPS PyPI，可用 `-IndexUrl https://.../simple` 指定可信镜像。
依赖冲突、缺少 npm、容器未就绪等错误均使安装失败，不输出安装成功。

数据库生命周期与 App Schema 所有权见[统一 bootstrap 与迁移](database-bootstrap.md)。启动只检查数据库就绪，不再自动迁移或填充规则目录。

## 日常操作

```powershell
.\scripts\windows\start.ps1 -SkipLangfuse -CheckOnly # 预检，不重启
.\scripts\windows\start.ps1 -SkipLangfuse            # 保留已有 App 启用状态
.\scripts\windows\verify.ps1                         # API、前端、Worker、Beat 和 App 部署检查
.\scripts\windows\stop.ps1                           # 停本机进程与容器，保留数据卷
```

工作台 `http://localhost:3000`，BFF 8001，Gateway 8000，MinIO 控制台 9001。
容器宿主端口仅绑定 `127.0.0.1`。日志仍在 `.runtime/test/logs/`，PID 在 `.runtime/test/`；
本次保留运行目录名，使旧启动器和 Beat PID 清理能够连续工作。
启动器统一使用 `127.0.0.1` 连接本机数据库、队列和对象存储，避免 Windows 的 `localhost`
优先尝试 IPv6 导致连接等待；该处理只改变进程环境，不改写已有 `.env`。
`scripts/test/*.ps1` 的原启动入口只作转发，部署实现不再维护两套。

| 操作 | 命令参数 |
| --- | --- |
| 只更新 Python 依赖 | `install.ps1 -SkipDocker -SkipNpm`；先停止应用进程 |
| 前端热更新开发 | `start.ps1 -SkipLangfuse -DevMode`；不能同时启用 App |
| 容器已经启动 | `start.ps1 -SkipMiddleware -SkipLangfuse` |
| 只停应用、保留容器 | `stop.ps1 -KeepMiddleware -KeepLangfuse` |
| 查看容器状态 | `containers.ps1 -Action status -WithLangfuse` |
| 拉取/启动容器 | `containers.ps1 -Action pull` / `containers.ps1 -Action up` |

启动保留 App 摘要验证及停用排空门禁，不能用重启跳过已启用包的升级流程。
已有账号的环境不要重复执行 `seed_auth_users`。执行环境验收并不代替真实设备巡检验收。

## 可选 Langfuse

```powershell
.\scripts\windows\install.ps1 -WithLangfuse
.\scripts\windows\start.ps1 # 未传 SkipLangfuse 时启动 observability profile
```

Langfuse UI 为 `http://localhost:3001`。沿用现有 v2 服务，不在本次更换观测平台版本。
配置 `.env` 中的 `LANGFUSE_HOST/PUBLIC_KEY/SECRET_KEY` 才会启用业务追踪；仅启动容器不代表追踪已经接入。
Python 观测依赖清单 `deployment/requirements/observability.txt` 保留为可选扩展，不是过期文件。
现有 Langfuse 初始化账号及开发密钥保持兼容，仅绑定本机；正式使用前应配置独立凭据。

## 旧部署迁移与依赖清理

原 PostgreSQL、Redis、RabbitMQ、MinIO 继续使用 `deployment_*` 数据卷；
Langfuse 数据卷仍为 `langfuse_langfuse_pg_data`。首次启用观测 profile 时，脚本检查旧容器归属和卷名，
只重建旧 `langfuse` 项目的两个容器，保留数据库卷。发现未知归属或数据库卷会停止，避免接到空库。
停止仅针对所选服务执行 `compose stop`，不会删除数据卷，也不会清理其他 profile 的容器。
修改 `.env` 里的数据库密码不会自动更改已有 PostgreSQL 卷里的账号密码。

旧 venv 可能残留已经退出项目的知识库依赖。`pip check` 报此类冲突时，不逐个猜测卸载，改用干净环境：

```powershell
.\scripts\windows\stop.ps1 -KeepMiddleware -KeepLangfuse
Rename-Item -LiteralPath .\venv -NewName ('venv-backup-' + (Get-Date -Format yyyyMMddHHmmss))
.\scripts\windows\install.ps1 -SkipDocker -SkipNpm
.\scripts\windows\bootstrap.ps1 -Action apply
.\scripts\windows\start.ps1 -SkipLangfuse
```

确认新环境和业务正常后再删除自己的旧 venv 备份。不能直接把其他目录里的 venv 移成 `venv`，其入口可能包含绝对路径。
根 `requirements.txt` 是完整安装入口，已包含 Django BFF 和开发验证工具；容器的精简角色清单统一放在 `deployment/requirements/`。
App 精确版本由 `src/apps/*/requirements.lock` 合并到 `.runtime/test/app-requirements.lock` 后一并安装。
保留 core、gateway、ai-worker、bootstrap 角色及可选 observability 清单；原 AI 和 documents 中间清单已合并到 ai-worker，Windows 包装清单已合并到根入口。
前端仅保留 npm 的 `package-lock.json`；早期 DSH 试验锁、pnpm 锁、分散的 Windows Compose、
Supervisor 启动器及重复的 `scripts/prod/*.ps1` 已退出。

## 备份

停止应用写入后执行 `scripts/windows/backup.ps1`，可加 `-WithLangfuse`。
输出 PostgreSQL 二进制备份（含 Django schema）、MinIO 卷归档及清单，失败不会生成完成清单。
单独妥善保存 `.env`；备份脚本不把密钥写入清单。恢复时配套恢复数据库和对象存储，并核对 App 摘要。
普通停机、升级和回退禁止使用 `docker compose down -v`。

## 本次验证记录

- 干净 Python 3.13 环境安装基础依赖、App 锁定依赖及可选 Langfuse，`pip check` 无冲突。
- 实际执行新安装入口的 Python 安装流程，Gateway/Worker 导入及 Django 系统检查通过。
- 19 项 Python 部署与兼容检查、33 项 PowerShell 进程/PID/IPv4 检查通过；18 个脚本语法检查通过。
- 本机已用统一 Compose 迁移并重启；原 PostgreSQL、MinIO、Langfuse 数据卷名称保持一致。
- 前端生产构建、API 与 7 个已启用 App 的 Gateway/前端/Worker/Beat 一致性检查通过。
- 未在本机重新安装操作系统级 Python、Node.js、Docker Desktop；这些软件已经存在。
- 运行中的原 `venv` 保留，里面已有的旧知识库/观测依赖冲突没有通过在线卸载处理；
  全新安装清单验证无冲突，旧环境需要清理时按上面的停机重建步骤操作。

## 50 人团队容量配置

8 核／32 GB／SSD 使用 `start.ps1 -PatrolWorkers 6`（默认即 6）。启动六个独立的 Windows solo 巡检进程；
Core Worker 专门处理控制/心跳，AI Worker 独立，避免长任务阻塞控制队列。
巡检日志是 `.runtime/test/logs/celery-patrol-1.log` 至 `celery-patrol-6.log`。
`-PatrolWorkers` 支持 1～12，增加执行槽必须同时核对数据库连接和主机内存，不能直接据此承诺吞吐量。
SQLAlchemy 的池等待/连接超时和容量可以通过 `.env` 的 `POSTGRES_POOL_*`、`POSTGRES_MAX_OVERFLOW`、
`POSTGRES_CONNECT_TIMEOUT` 配置；启动器为每个 Celery 进程固定使用 3 常驻＋2 临时连接。
生产运行和本地回放的区别、压测命令与结果见[团队容量计划](team-capacity-plan.md)。

普通启动关闭 DEBUG；仅 `-DevMode` 开启调试。生产限流按已验证的用户身份分配额度，
`BFF_RATE_LIMIT_DEFAULT=600`、`BFF_RATE_LIMIT_CHAT=30`、`BFF_RATE_LIMIT_WINDOW=60` 分别控制普通请求、聊天请求和秒数窗口。
同一办公网的登录用户不共用 IP 额度；匿名普通请求仍限制为每 IP 每分钟 60 次。
Worker 的内部准入回调必须通过服务签名验证，才免于重复计数。
