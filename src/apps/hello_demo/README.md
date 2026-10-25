# 演示应用 hello-demo

平台上最小的能力包：一个清单、一个后端接口、一个前端页面，没有任务、数据库和 Agent 工具。
开发手册（工作台“Skills 和连接器 → 开发指南”）
用它演示应用从开发、上线、升级到下线的全过程。

默认未启用。自己试一下（`-ApplyDatabase` 先登记当前代码，`-EnableApps` 再启用）：

```powershell
.\scripts\windows\stop.ps1 -KeepMiddleware -KeepLangfuse
.\scripts\windows\start.ps1 -ApplyDatabase -EnableApps hello-demo -NoBrowser
```

然后打开 http://localhost:3000/apps/hello-demo 。下线：

```powershell
venv\Scripts\python.exe web\django_backend\manage.py app_packages disable hello-demo
```

需要任务、数据库或 Agent 工具时，参考 `src/apps/delivery_probe/`。
