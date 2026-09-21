# 能力包接入验证

P2 的独立交付样例，默认未安装、未授权。包含前端、API、真实数据库表、
两次增量迁移、异步任务、周期性派发恢复和 Agent 工具。
文本转换不调用模型、不连接设备。

复制整个 `delivery_probe` 目录到另一套兼容 NetOps 的 `src/apps/` 即可发现。
无需修改宿主业务路由或任务列表；仍须执行安装迁移、构建、重启相关进程和启用检查。
这不是运行中的前后端代码热加载，也不是不可信插件沙箱。

```text
python web/django_backend/manage.py app_packages install delivery-probe
npm --prefix web/react_frontend run build
python -m src.platform.kernel.worker --queues netops.default --loglevel=info
python web/django_backend/manage.py app_packages enable delivery-probe --gateway-url http://127.0.0.1:8000 --frontend-url http://127.0.0.1:3000
python web/django_backend/manage.py app_packages disable delivery-probe
python web/django_backend/manage.py app_packages drain delivery-probe
```

生产需运行平台的 AppScheduler Beat；管理员单独授予使用权。
`requirements.lock` 声明示例包额外 Python 依赖的精确版本，`package.json` 声明共享前端依赖的精确版本；
安装/构建检查冲突，统一环境升级由部署者处理，运行时不自动 pip/npm install。

升级仅执行 `migrations/versions` 中尚未应用的迁移，独立版本表为
`alembic_app_delivery_probe`。原平台历史迁移链不变。
停用取消排队和过期租约，保留仍在执行的租约直至任务结束或过期，再次 drain 收尾。
数据与历史迁移永久保留，不提供自动删除包或业务表的命令。
