# Windows 部署

应用进程（FastAPI 网关、Django BFF、React 前端、Celery Worker 与 Beat）运行在本机；PostgreSQL、Redis、RabbitMQ、MinIO 和可选的 Langfuse 运行在 Docker Desktop。所有入口都在 `scripts/windows/`，中间件只使用 `deployment/compose.windows.yml`。

## 环境要求

- Windows 10/11 x64
- Python 3.13 x64
- Node.js 22 及以上
- Docker Desktop，使用 Linux containers（WSL 2 后端）
- 默认启动 6 个巡检 Worker，这个配置按 8 核、32 GB 内存、SSD 验证过；机器较小时用 `-PatrolWorkers` 调低

缺少的软件可以用下面的脚本通过 WinGet 安装，已安装的保持不变。安装器要求提权或重启时按提示操作，完成后重新打开 PowerShell，启动 Docker Desktop 并等待引擎就绪：

```powershell
.\scripts\windows\prerequisites.ps1
```

如果 PATH 里的 `python` 不是 Python 3.13（例如 Windows 应用商店的占位程序），在下面的安装命令后加 `-PythonCommand "C:\...\Python313\python.exe"`。

## 首次安装

在仓库根目录的 PowerShell 中依次执行：

```powershell
# 1. 创建 venv、安装 Python 与各应用的依赖、npm ci、拉取并启动中间件容器
.\scripts\windows\install.ps1

# 2. 初始化数据库：平台迁移、Django 迁移、各应用的迁移与初始数据
.\scripts\windows\bootstrap.ps1 -Action apply

# 3. 启动全部服务，并启用需要的应用
.\scripts\windows\start.ps1 -SkipLangfuse -EnableApps device-management,inspection,rule-governance,device-access,netconf-api,topology,firewall-policy,resource-ledger,official-documents,materials,assistant

# 4. 创建第一个管理员
.\venv\Scripts\python.exe web\django_backend\manage.py createsuperuser
```

打开 `http://localhost:3000`，用管理员登录。之后在“系统管理”里创建用户、用户组并授予应用使用权。

说明：

- 第一次运行 `install.ps1` 时，如果没有 `.env`，会从 `.env.example` 生成，并自动填好随机的认证密钥、内部调用密钥、数据库和 MinIO 密码，以及设备凭据加密密钥。已有的 `.env` 原样保留。不要在安装前手工复制含 `change-me` 的模板。
- AI 功能（规则自动制作、公文写作、知识问答）需要在 `.env` 中填写 `DEEPSEEK_API_KEY`，改完后重新运行 `start.ps1`。
- 默认 pip 源是 PyPI，可用 `-IndexUrl https://.../simple` 换成可信的镜像。
- 安装不会创建演示账号、不会修改授权，也不会自动启用应用；应用由 `-EnableApps` 显式启用，以后启动会保留启用状态。
- 应用之间的依赖：`topology` 依赖 `device-access`、`rule-governance`、`netconf-api`；`netconf-api` 依赖 `device-access`、`rule-governance`；`assistant` 依赖 `materials`。

## 访问地址

| 服务 | 地址 |
| --- | --- |
| 工作台（React） | `http://localhost:3000` |
| Django BFF | `http://localhost:8001` |
| FastAPI 网关 | `http://localhost:8000` |
| MinIO 控制台 | `http://localhost:9001` |
| Langfuse（可选） | `http://localhost:3001` |

容器端口只绑定 `127.0.0.1`。日志在 `.runtime/test/logs/`，进程 PID 在 `.runtime/test/`。

## 从其他电脑访问

其他电脑用 `http://<这台电脑的地址>:3000` 打开工作台。页面、接口、WebSocket 和文件上传都只走这一个端口：上传文件时，浏览器把文件发到工作台的 `/object-storage/`，由前端服务原样转给本机的 MinIO，MinIO 不需要对外开放。`start.ps1` 结束时会列出本机可用的访问地址。

在 Windows 防火墙里放行 TCP 3000（管理员 PowerShell，执行一次）：

```powershell
New-NetFirewallRule -DisplayName "NetOps 工作台" -Direction Inbound -Protocol TCP -LocalPort 3000 -Action Allow
```

## 日常操作

```powershell
.\scripts\windows\start.ps1 -SkipLangfuse            # 启动，保留已启用的应用
.\scripts\windows\start.ps1 -SkipLangfuse -CheckOnly # 只做启动预检
.\scripts\windows\verify.ps1                         # 检查 API、前端、Worker、Beat 和应用部署
.\scripts\windows\stop.ps1                           # 停止本机进程和容器，保留数据卷
```

| 场景 | 命令 |
| --- | --- |
| 只停应用、保留容器 | `stop.ps1 -KeepMiddleware -KeepLangfuse` |
| 容器已经在运行 | `start.ps1 -SkipMiddleware -SkipLangfuse` |
| 前端热更新开发 | `start.ps1 -SkipLangfuse -DevMode`（不能同时启用应用） |
| 只更新 Python 依赖 | 先停应用，再 `install.ps1 -SkipDocker -SkipNpm` |
| 查看或启动容器 | `containers.ps1 -Action status` / `containers.ps1 -Action up` |
| 调整巡检并发 | `start.ps1 -PatrolWorkers 4`（1～12，默认 6） |

## 升级应用

应用包的代码有改动时，平台会拒绝直接运行旧的启用记录，必须先停用排空再升级：

```powershell
.\scripts\windows\stop.ps1 -KeepMiddleware -KeepLangfuse
.\scripts\windows\start.ps1 -SkipLangfuse -UpgradeApps rule-governance,netconf-api,topology -NoBrowser
```

依赖被升级应用的其他已启用应用要一起列进 `-UpgradeApps`，被依赖的放在前面；否则会提示 `Disable dependent applications first`。升级前先备份数据库。

## 可选：Langfuse

```powershell
.\scripts\windows\install.ps1 -WithLangfuse
.\scripts\windows\start.ps1
```

不加 `-SkipLangfuse` 时会一起启动 Langfuse。在 `.env` 中配置 `LANGFUSE_HOST`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY` 后才会记录调用追踪。

## 备份

停止应用写入后执行 `.\scripts\windows\backup.ps1`（使用 Langfuse 时加 `-WithLangfuse`），输出 PostgreSQL 备份、MinIO 卷归档和清单。`.env` 需要单独妥善保存。恢复时数据库和对象存储要一起恢复。普通停机、升级和回退都不要使用 `docker compose down -v`，它会删除数据卷。

## 依赖冲突时重建 venv

`install.ps1` 发现 venv 里有依赖冲突会停止。此时换一个干净的 venv：

```powershell
.\scripts\windows\stop.ps1 -KeepMiddleware -KeepLangfuse
Rename-Item -LiteralPath .\venv -NewName ('venv-backup-' + (Get-Date -Format yyyyMMddHHmmss))
.\scripts\windows\install.ps1 -SkipDocker -SkipNpm
.\scripts\windows\bootstrap.ps1 -Action apply
.\scripts\windows\start.ps1 -SkipLangfuse
```

确认正常后再删除旧的 venv 备份。不要把其他目录里的 venv 直接改名成 `venv`，其中的入口带有绝对路径。
