# 能力包安装、升级与启用

核对日期：2026-09-22。`src/apps/<package>/app.yaml` 描述包 ID、版本、依赖、前端、API、工具、任务与数据库契约。清单内容和实际部署摘要必须一致。

## 本地部署

新增包先复制完整目录、安装其锁定依赖并执行迁移，再构建和启动服务。使用项目既有脚本：

```powershell
.\scripts\test\install.ps1 -SkipDocker -SkipNpm
.\scripts\test\start.ps1 -CheckOnly
.\scripts\test\start.ps1 -EnableApps materials,assistant -NoBrowser
```

首次启用多个包按依赖关系验证。启动脚本保留既有启用/授权状态，不自动启用停用的 App。开发 `-DevMode` 与正式 `-EnableApps` 不组合使用。

## 升级与排空

更新启用包之前，用当前版本代码关闭准入并排空，再替换文件。先检查在途任务和维护窗口；停用的业务收尾行为由各包生命周期定义，不等同于任意任务都会自然完成。

```powershell
venv\Scripts\python.exe web/django_backend/manage.py app_packages status --compact
venv\Scripts\python.exe web/django_backend/manage.py app_packages disable APP_ID
venv\Scripts\python.exe web/django_backend/manage.py app_packages drain APP_ID
# 确认停用/排空后更新该包，再启动并显式启用：
.\scripts\test\start.ps1 -EnableApps APP_ID -NoBrowser
```

`APP_ID` 替换为实际清单 ID。更底层 CLI 的 prepare/install/upgrade/verify/enable 行为以 `app_packages --help` 和[实现](../web/django_backend/bff/management/commands/app_packages.py)为准。启用会校验 Gateway、前端实际资源、Worker/队列及 Beat；数据库迁移、依赖或摘要失败时不能绕过门禁。

App 使用权通过用户/用户组授权。运行、下载、Agent 工具和历史读取使用同一准入边界；原有数据归属约束继续生效。停用不等于删除业务数据，代码回退也不自动回退 schema。

生产部署还需目标环境镜像、网络与服务地址验证，见[部署指南](10_部署安装手册.md)。仅复制目录不能证明应用已就绪。
