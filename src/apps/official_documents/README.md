# 公文写作能力包

公文的业务实现、前端、排版/生成代码、模板、参考资料和测试均在本目录。
依赖 NetOps 1.x 的身份授权、SQLAlchemy 会话、Conversation/Message、Celery 与 MinIO。

`app.yaml` 注册 API、前端入口、工具、任务与周期性对账。原
`src/core/official_documents`、`src/skills/official_document` 和前端旧路径仅作兼容转发。
旧 Skill 资源仍保留供旧目录发现/历史描述使用；运行时读取本包资源。

新 API：`/api/apps/official-documents`。旧 `/api/v1/official-documents` 和
BFF `/api/official-documents/` 兼容到同一实现，所有入口均检查当前应用授权。
原旧聊天同步 Skill/test-run 不再直接生成文件；后续 Agent 适配器使用包工具。

## 部署

1. 为 Django、Gateway、Worker 配置相同的 `NETOPS_APP_INTERNAL_SECRET`（至少32字符）。
   本地 `.env` 可用；容器中 `NETOPS_AUTHORITY_URL` 应指向 `http://django:8001`。
2. 先备份，运行 `python web/django_backend/manage.py migrate`。
3. 首次运行 `python web/django_backend/manage.py app_packages install official-documents`。
   从 P1 升级先执行 `app_packages disable official-documents`、重复 `drain` 至 disabled，
   再执行 `app_packages upgrade official-documents`。保留原 AppGrant。
4. 前端 `npm run build` 自动生成注册表和产物校验清单；部署 Gateway、前端、Worker 和 Beat。
   AI Worker 通过 `python -m src.platform.kernel.worker --queues netops.ai` 自动追加包队列。
5. 运行 `python web/django_backend/manage.py app_packages enable official-documents --gateway-url http://127.0.0.1:8000 --frontend-url http://127.0.0.1:3000`。
   每个 Gateway 实例均须分别提供 `--gateway-url`；前端 URL 必须实际提供本次生产构建。
6. 管理员在“用户管理 → 应用使用权限”给用户/组授权。迁移已有公文写入用户时可显式使用
   `sync_apps --grant-legacy-document-writers`，仅保留当前 operator 用户的写入权，不授权 viewer。

P2 启用会验证代码摘要、前端实际文件、队列消费者、任务注册和 Beat 心跳。
`sync_apps --enable` 已转到同一检查，不能跳过部署验证。
公文继续使用原有业务表及历史 Alembic 链（`database.mode: legacy`），不重复建表。

## 回退

停用入口：`app_packages disable official-documents`，使用 `app_packages drain official-documents`
复查排空。保留代码直到任务排空。已经安装的完整包归档在 `.runtime/app-package-releases/`。
若回退到上一程序版本，新增 Django App 元数据表可保留，不动原公文业务表；
旧版本本身不具备 App 授权模型，回退操作应由平台管理员在维护窗口执行。
