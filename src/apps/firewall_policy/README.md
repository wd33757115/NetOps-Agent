# 防火墙策略业务能力包

`firewall-policy` 1.0.0 包含策略计算、拓扑证据、配置校验、预览、申请表导出、变更工单、前端、API、任务、独立迁移和 Agent 工具。`app.yaml` 是唯一平台注册入口。复制本目录并按平台安装流程构建、安装、校验和启用即可接入；不在运行时安装依赖。

## 使用

- 页面：`/apps/firewall-policy`。上传 XLSX，可选 JSON 拓扑，填写工单号并生成。
- 任务提交后地址携带 `?run=...`，可关闭页面并从历史记录恢复。取消关闭任务，重试创建新任务并保留原记录。
- 配置包、预览和申请表来自同一份已校验计划；工单引用服务端的完成计划，客户端只可填写工单头。只生成文件，不下发设备、不提交 ITSM 审批。
- 默认拓扑使用包内 `backend/engine/policy/scripts/topology.json`，提交时保存快照。生产使用前应核对该拓扑或上传自己的拓扑。

## API 与权限

所有入口均在 `/api/apps/firewall-policy`，由平台统一校验 App 授权，不设置应用内角色或功能权限。个人上传和任务保持账号数据隔离；不提供跨账号代操作。

| 接口 | 用途 |
|---|---|
| `GET /template` | 下载空白申请模板 |
| `POST /uploads?filename=...&kind=policy或topology&request_id=...` | 原始二进制上传，最大 10 MiB |
| `POST /runs` | 上传 ID、工单号、合并和业务路径选项；request_id 幂等 |
| `GET /runs?offset=0&limit=20` | 当前账号策略任务历史 |
| `GET /runs/{id}` | 任务状态、结果、最近 50 个工单子任务 |
| `POST /runs/{id}/tickets` | request_id 和 fields；拒绝传入 manifest、命令或任意 URL |
| `POST /runs/{id}/cancel` | 取消 queued/running 任务 |
| `POST /runs/{id}/retry` | 使用新的 request_id 重试失败或取消的任务，返回新任务 |
| `GET /runs/{id}/artifacts/{key}` | 重新授权并校验 SHA-256 后下载，不发布 MinIO 签名 URL |

Agent 工具与 HTTP API 调用同一业务服务，身份由平台提供。文件需先由应用上传接口接收，不能将聊天附件路径当作上传 ID。

## 持久化与执行

包拥有 `app_firewall_uploads`、`app_firewall_runs` 和独立迁移链 `firewall_policy_0001`。文件存入私有 MinIO `apps/firewall-policy/`；数据库只记录校验值和私有对象键。

队列为 `app.firewall-policy`。30 秒补偿扫描保证已入库但未成功投递的任务可恢复。60 秒租约，算法进程每 5 秒检查任务和授权，计算上限 300 秒；每个任务最多自动执行 3 次。失败或超时不会发布半成品。撤销授权、停用 App 后拒绝新请求，Worker 检查撤权并关闭任务；旧 Worker 的迟到结果不能覆盖新租约。停用保留数据和历史产物。

平台依赖：PostgreSQL、MinIO、Celery Broker、Django 授权服务及 `NETOPS_APP_INTERNAL_SECRET`。Worker/Beat 使用平台动态队列与调度配置，生产启用必须通过 Gateway、Worker、Beat 和前端构建摘要检查。

文件与业务记录暂不自动删除；中断或并发上传可能留下未引用对象，需按数据库引用核对后由运维清理。备份需同时覆盖业务数据库和 MinIO。禁止删除仍被运行任务、上传或完成产物引用的对象。

## 兼容边界

旧 `src/skills` 脚本路径是到本包的兼容导入/CLI 入口，不再维护第二份实现。旧 Skill/test-run 和旧工作流中的对应 Skill 调用返回应用入口提示，避免绕过 App 授权。历史 Skill 产物保留原路径，未自动转换为新任务；旧 ITSM 自动流程尚未重写为本包调用。宿主页面及服务文件仅保留兼容导出。

验证见 `tests/unit/test_firewall_package.py`、原策略算法回归、`web/django_backend/bff/test_firewall_deployment.py` 和 `web/react_frontend/e2e/firewall-policy.spec.ts`。真实交付测试要求显式设置独立的 `TEST_PACKAGE_BROKER_URL`，会在本地 MinIO 创建随机对象并清理，不使用生产队列。
