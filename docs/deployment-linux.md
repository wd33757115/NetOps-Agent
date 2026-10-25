# Linux 部署（Docker Compose）

所有服务都运行在容器里：PostgreSQL、Redis、RabbitMQ、MinIO、一次性的数据库迁移作业、Django BFF、React 前端（Nginx）、FastAPI 网关、巡检与控制 Worker、Beat，以及可选的 AI Worker 和 Langfuse。

## 准备

- Docker Engine 与 Docker Compose v2.24 及以上（生产覆盖文件使用 `!override` 标签）。
- 在仓库根目录执行下面所有命令。

从模板创建配置，填好密钥、数据库和对象存储密码、模型密钥：

```sh
cp .env.example .env
bash scripts/prod/check_env.sh
```

必须填写或更换的项：

- `SECRET_KEY`、`JWT_SECRET_KEY`、`ITSM_WEBHOOK_SECRET`：随机长字符串。
- `NETOPS_APP_INTERNAL_SECRET`：至少 32 个字符，应用之间的内部调用用它签名。
- `POSTGRES_PASSWORD`、`MINIO_ACCESS_KEY`、`MINIO_SECRET_KEY`：数据库和对象存储的账号密码，第一次启动时写入数据卷，之后改 `.env` 不会改已有账号。
- `DEVICE_ACCESS_CREDENTIAL_KEY`：Fernet 密钥，SSH 会话密码和临时设备凭据用它加密。生成：`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`。
- `DEEPSEEK_API_KEY`：AI 功能（规则自动制作、公文、问答）需要。

`check_env.sh` 对仍是示例值或弱口令的项报错，对没填的项给出警告。

## 选择组合

| Compose 文件与 profile | 内容 |
| --- | --- |
| `docker-compose.yml` + `core` | 中间件、迁移作业、Django、React、FastAPI、巡检/控制 Worker 与 Beat |
| 再加 `docker-compose.ai.yml` + `ai` | AI 网关与 `netops.ai` Worker；规则生成、公文和问答需要 |
| 再加 `docker-compose.prod.yml` | 已发布端口只绑定 `127.0.0.1`，由外层反向代理对外提供 HTTPS |
| `observability` profile | 可选的 Langfuse |

## 启动

```sh
docker compose --env-file .env -f deployment/docker-compose.yml -f deployment/docker-compose.ai.yml -f deployment/docker-compose.prod.yml --profile core --profile ai config --quiet
docker compose --env-file .env -f deployment/docker-compose.yml -f deployment/docker-compose.ai.yml -f deployment/docker-compose.prod.yml --profile core --profile ai up -d --build
```

也可以用 `scripts/prod/start.sh`（`WITH_AI=1` 时叠加 AI 组合）和 `scripts/prod/stop.sh`。

`migrate` 是唯一的数据库初始化作业，执行 `python -m src.platform.database.bootstrap apply`：依次完成平台迁移、Django 迁移、各应用的迁移和初始数据，其他服务等它成功后才启动。迁移失败时先看 `docker compose logs migrate`，修复后重新 `up`，已完成的阶段会跳过。

创建第一个管理员：

```sh
docker compose --env-file .env -f deployment/docker-compose.yml exec django python manage.py createsuperuser
```

## 启用应用

`migrate` 会安装全部应用包，但不会启用。逐个启用需要的应用，被依赖的放在前面：

```sh
DC="docker compose --env-file .env -f deployment/docker-compose.yml -f deployment/docker-compose.ai.yml"
for app in device-management inspection rule-governance device-access netconf-api topology firewall-policy resource-ledger official-documents materials assistant; do
  $DC exec django python manage.py app_packages enable $app --gateway-url http://fastapi:8000 --frontend-url http://react
done
$DC exec django python manage.py app_packages status --compact
```

启用时会检查网关、前端、Worker 和 Beat 都已加载这个应用。依赖关系：`topology` 依赖 `device-access`、`rule-governance`、`netconf-api`；`netconf-api` 依赖 `device-access`、`rule-governance`；`assistant` 依赖 `materials`。之后在工作台的“系统管理”里创建用户、用户组并授予应用使用权。

## 验证

1. `docker compose ... ps`：`migrate` 已成功退出，其余服务健康。
2. `curl http://127.0.0.1:8001/api/health/` 返回正常；`/api/health/diagnostics/` 中 PostgreSQL、MinIO、Worker 队列和 Beat 都可用。
3. 浏览器打开 `http://<主机>:3000`（生产组合下经反向代理访问），用管理员登录；从其他电脑访问前先按下文配置 `DJANGO_ALLOWED_HOSTS`。

对外 HTTPS 反向代理可参考 `deployment/nginx.production.conf.example`。反向代理在其他容器里时，通过明确的 Docker 网络连接，不要依赖宿主机的 loopback 绑定。

## 从其他电脑访问

浏览器只访问前端（`3000` 端口，或外层反向代理的 HTTPS 地址）。页面、接口、WebSocket 和文件上传都经过它：上传文件时，浏览器把文件发到 `/object-storage/`，由前端容器原样转给 `minio:9000`，MinIO 不需要对外开放。

Django 只接受 `DJANGO_ALLOWED_HOSTS` 里的主机名。用服务器 IP 或域名访问时，把它们加进 `.env`，再重新 `up -d`：

```sh
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,10.0.0.5,netops.example.com
```

## 备份与升级

- `scripts/prod/backup.sh` 导出 PostgreSQL 和 MinIO，并生成清单。数据库和对象存储必须一起备份、一起恢复。
- 升级：备份 → 拉取新代码 → `up -d --build`。`migrate` 会执行新的迁移。
- 不要用 `docker compose down -v`：它会删除数据卷。
