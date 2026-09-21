<!-- document-status: archived -->
> 历史版本交付/设计记录；当时的部署状态、待办和测试数量不代表当前版本。当前状态见[实施状态](netops-architecture-implementation-plan.md)。

# P2：能力包交付和生命周期

本批承接 `netops-architecture-implementation-plan.md` 的 P2。四项真实业务中仍只有公文完成归包；
第二个包 `src/apps/delivery_probe` 是交付验收样例，不代表防火墙或巡检已迁移。

## 交付内容

- 清单摘要升级为包内容摘要，覆盖实现、前端、资源、迁移和依赖声明；忽略缓存和测试产物，统一 CRLF/LF，避免跨系统误报。
- 包内 Alembic 版本目录配合平台共享 env；每包独立版本表。支持新库、增量升级、重复执行、事务回滚与失败后重试。
- legacy 模式保留旧迁移链；公文声明最低历史版本和表所有权。未来 handoff 必须声明交接 revision、继承表和列的类型/nullable 合同；不会自动 stamp 或修改旧迁移链。
- `app_packages` CLI 提供 install、upgrade、verify、enable、disable、drain、status。安装失败保留上次成功版本和错误；启用必须验证实际部署。
- PostgreSQL 管理锁和 SQLite 文件锁串行化生命周期操作；包数据库另有迁移事务锁。
- Gateway 暴露经服务凭据保护的启动摘要与活动请求/工具计数；Worker 通过 Celery inspection 返回启动摘要、已注册任务和实际消费队列。旧进程读取新文件不能伪装已重新加载业务代码。
- 前端生产构建生成 `netops-apps.json`，验证包含包摘要和实际部署 JS/CSS/HTML 的 SHA-256；功能开关关闭的包不进入可启用名单。
- Beat 使用 `AppScheduler`，仅对启用且版本匹配的包派发周期任务；Redis 心跳证明实际加载版本，多个活跃包调度器阻止启用，避免重复派发。
- Worker 启动器从清单追加所有包队列。本地 AI 启动脚本和 Compose AI overlay 已使用该入口，原确定性巡检/control 队列不混入包队列。
- Python 包依赖精确锁定、构建时合并；冲突使构建失败。前端依赖对齐宿主 package-lock，并检查实际 node_modules 版本。运行时不安装依赖。
- 停用立即关入口和周期派发；等已接纳的请求/工具结束后取消排队/过期租约，等待有效租约结束，再进入 disabled。保留结果、历史表及包归档，不自动删除代码或数据。
- 修复通用 BFF 尾斜杠重定向导致 POST 变为 GET 的问题。

状态流为 `discovered → installing → installed → enabled`，失败进入 `failed`；
停用为 `enabled → draining → disabled`。`installed` 表示迁移完成但部署尚未核验，
不会单凭代码发现就授予访问。此实现将计划中的 migrating/ready 中间步骤合并在事务和启用检查中。

## 交付新包

1. 将可信完整目录复制到 `src/apps/<python_name>`；清单声明入口、任务、资源、表所有权及必要依赖。
2. `python -m src.platform.kernel.cli validate`；为包添加精确的 `requirements.lock` 和按需的 `package.json`。
3. Docker 构建会合并 Python 包依赖。宿主环境可先运行下方约束生成与安装命令。
   前端新增库须经 `npm install --save-exact <name>@<version>` 更新宿主统一锁；不需要改中央业务路由/任务清单。
4. 备份数据库；执行 Django 元数据迁移及 `app_packages install <id>`。
5. 构建前端、重启 Gateway/Worker/Beat。新增目录和升级源码仍是构建部署操作，不是运行时热插拔。
6. `app_packages enable` 检查真实服务后启用，再在用户管理页授予用户/组使用权。

```powershell
python -m src.platform.kernel.build_constraints --output .runtime/app-requirements.lock
python -m pip install -r .runtime/app-requirements.lock
python web/django_backend/manage.py migrate
python web/django_backend/manage.py app_packages install delivery-probe
npm --prefix web/react_frontend run build
# 按现有部署方式启动 Gateway、生产前端、Worker 和 Beat 后：
python web/django_backend/manage.py app_packages enable delivery-probe --gateway-url http://127.0.0.1:8000 --frontend-url http://127.0.0.1:3000
python web/django_backend/manage.py app_packages status
```

`--gateway-url` 要列出所有能承接请求/工具的实例地址；CLI 无法从单个负载均衡 URL
推导其背后的全部实例。新增 Agent 执行进程时也必须纳入活动调用的部署/排空管理，属于 P6 接入要求。
后台任务必须有持久状态和幂等执行语义，声明 drain/pending 钩子；停用后残余 broker 消息只能安全地跳过已终态记录。
完全卸载 Worker/代码、清理队列或历史数据属于独立维护操作，不由 disable 自动执行。

## 升级与故障恢复

升级前用旧版本关闭入口并完成 drain，然后更换目录、执行 upgrade、重建和重启、最后 enable。
同一内容可重复 install/upgrade。迁移失败可修复后重试；不自动 stamp、不自动降级数据库。
一批安装按包依赖顺序执行；后一个包失败不回滚前一个已成功的独立包。

安装代码归档：`.runtime/app-package-releases/<app-id>/<digest>.zip`。
归档与数据库备份需要一同进入运维备份。包迁移应保持数据库事务性，不得在迁移中产生不可回滚的外部副作用。
历史链交接必须由后续业务迁移明确停止旧链对已交接表的写入；当前没有将公文表转入新链。

## 已验证与边界

- SQLite 和 PostgreSQL 独立测试 schema 均覆盖新安装、已有行升级、重复执行、DDL 失败回滚与重试；已有无版本表的同名业务表会被拒绝接管。
- 隔离 Redis 容器、真实 Worker/Beat 子进程、真实 HTTP Gateway/BFF、前端生产文件：未启动消费者时拒绝启用，启动后启用，提交并消费任务，工具读取结果，停用取消排队任务。
- 浏览器验证新包路由、提交、刷新恢复、无授权门禁；既有公文浏览器回归保留。浏览器 API 为夹具；真实队列链路使用独立集成测试。
- 混合 Worker 版本、缺消费者、前端实际字节不匹配、未授权部署探测、关闭周期任务和依赖冲突均有拒绝测试。
- 未做真实设备执行、真实模型生成或生产滚动发布；Docker 构建脚本已更新，但全套生产镜像重建不是本次测试的完成条件。

可重复运行：

```powershell
python -m pytest tests/unit/test_package_delivery.py tests/unit/test_app_package_kernel.py tests/unit/test_official_document_workspace.py
python web/django_backend/manage.py test bff.test_app_lifecycle bff.test_app_packages bff.test_app_package_roundtrip
# 使用已有本地 PostgreSQL 的全新随机 schema；测试结束只删除该 schema：
$env:TEST_PACKAGE_POSTGRES='1'
python -m pytest tests/unit/test_package_delivery.py
# 必须指向专为本次测试启动的 Redis，不能使用业务 broker：
$env:TEST_PACKAGE_BROKER_URL='redis://127.0.0.1:<隔离端口>/0'
python web/django_backend/manage.py test bff.test_package_deployment
```

实现参考：[Alembic 共享事务连接](https://alembic.sqlalchemy.org/en/latest/cookbook.html#sharing-a-connection-across-one-or-more-programmatic-migration-commands)、
[Celery 自定义检查命令](https://docs.celeryq.dev/en/stable/userguide/workers.html#writing-your-own-remote-control-commands)。
