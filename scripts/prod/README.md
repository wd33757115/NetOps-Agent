<!-- SPDX-FileCopyrightText: 2026 wangdong <wangdong5919@163.com> -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# 生产脚本说明

本目录提供 **上线前预检、一键启动、冒烟、备份** 工具，与 [docs/13_生产上线清单.md](../../docs/13_生产上线清单.md) 配套使用。

## 前置条件

- Docker Desktop / Docker Engine + Compose v2
- 项目根目录 `.env`（从 [`.env.example`](../../.env.example) 复制并修改密钥）
- 生产模式默认叠加 [`deployment/docker-compose.prod.yml`](../../deployment/docker-compose.prod.yml)（中间件仅绑定 `127.0.0.1`）

## 脚本一览

| 脚本 | 平台 | 作用 |
|------|------|------|
| `check_env.ps1` / `check_env.sh` | Win / Linux | 弱密钥与必填项预检 |
| `start.ps1` / `start.sh` | Win / Linux | Compose 启动 → migrate → seed → 冒烟 |
| `smoke_test.ps1` / `smoke_test.sh` | Win / Linux | BFF 健康、诊断、登录、Workflow 模板、viewer 403、React |
| `backup.ps1` / `backup.sh` | Win / Linux | PG dump + MinIO mirror + Django SQLite |
| `install.ps1` / `install.sh` | Win / Linux | 拉取基础镜像、构建应用镜像 |
| `stop.ps1` / `stop.sh` | Win / Linux | 停止 Compose 栈 |

## 推荐上线流程

### Windows (PowerShell)

```powershell
copy .env.example .env          # 编辑密钥与 DEEPSEEK_API_KEY
.\scripts\prod\start.ps1        # 生产模式（默认 prod overlay + 冒烟）
```

开发调试（端口对全网卡开放、跳过冒烟）：

```powershell
.\scripts\prod\start.ps1 -Dev -SkipSmoke
```

### Linux

```bash
cp .env.example .env
chmod +x scripts/prod/*.sh
./scripts/prod/start.sh
```

仅本地开发栈：

```bash
DEV=1 SKIP_SMOKE=1 ./scripts/prod/start.sh
```

## 冒烟测试

默认 BFF 地址 `http://127.0.0.1:8001`，演示账号来自 `seed_auth_users`（上线前务必改口令）：

```powershell
.\scripts\prod\smoke_test.ps1
# 自定义
.\scripts\prod\smoke_test.ps1 -BffBase "http://127.0.0.1:8001" -AdminPassword "your-password"
```

```bash
BFF_BASE=http://127.0.0.1:8001 ./scripts/prod/smoke_test.sh
```

## 备份与恢复

```powershell
.\scripts\prod\backup.ps1
# 输出: backups/YYYYMMDD-HHMMSS/
```

```bash
./scripts/prod/backup.sh
```

备份内容：

- `postgres-netops_agent.sql` — 业务库 + LangGraph checkpoint
- `minio-netops-files/` — MinIO 对象（Skill 产物等）
- `django-db.sqlite3` — Django 用户/RBAC（当前 SQLite 方案）
- `manifest.json` — 清单

**PostgreSQL 恢复示例：**

```bash
cd deployment
docker compose exec -T postgres psql -U netops -d netops_agent < ../backups/<ts>/postgres-netops_agent.sql
```

## 生产 Compose 说明

```bash
cd deployment
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

- FastAPI **不**映射宿主机 8000，仅内网；对外走 Django BFF `/api`
- Celery Worker 与 FastAPI 同镜像，Workflow / 异步 Skill 依赖其运行
- React 构建使用 `VITE_API_BASE_URL=/api`（见 `deployment/Dockerfile.react`）

## 外层 HTTPS

宿主机 Nginx 反代示例见 [`deployment/nginx.production.conf.example`](../../deployment/nginx.production.conf.example)：`443` → `127.0.0.1:3000`。

## 故障排查

1. `check_env.ps1` / `check_env.sh` — 密钥与 DEBUG 开关  
2. `curl http://127.0.0.1:8001/api/health/diagnostics/` — Celery Worker / Beat 心跳 / PG / MinIO
3. `docker compose logs celery-patrol celery-control celery-beat --tail 100` — Workflow 与周期调度
4. 详见 [docs/11_运维技术手册 & 故障排查.md](../../docs/11_运维技术手册%20&%20故障排查.md)
