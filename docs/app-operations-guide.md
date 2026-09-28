# 应用管理与巡检预检操作

核对日期：2026-09-27。适用于删除旧“平台模块”后的设备管理与网络巡检应用。本地页面地址以 `http://localhost:3000` 为例，远程部署请替换为实际站点地址。

## 功能入口

| 要做什么 | 当前入口 |
| --- | --- |
| 授予或撤销应用使用权 | [系统管理 → 应用授权](http://localhost:3000/system/apps) |
| 查看巡检运行环境 | [新建在线巡检](http://localhost:3000/apps/inspection?launch=1) → 运行环境详情 |
| 测试设备连接、查看连接记录 | [设备管理 → 设备资产](http://localhost:3000/apps/device-management) → 设备详情 → 测试连接 / 连接历史 |
| 配置采集命令 | [设备管理 → 配置命令集](http://localhost:3000/apps/device-management?tab=commands) |
| 查看将要执行的设备、命令和覆盖检查 | 新建在线巡检 → 预览设备与命令 |
| 查看 App 版本、启用或停用 App | 当前没有启停页面，使用下文后台管理命令 |

旧“平台模块”页面已删除，并未迁移成另一个集中管理页面。应用授权页面显示应用状态，但没有 App 启停按钮。

## 授权与撤销授权

1. 使用 `admin` 或 `netops-config` 账号，从工作台“系统管理”进入“应用授权”，也可直接打开上表地址。
2. 搜索目标应用，点击“管理授权”。
3. 添加直接授权用户或授权用户组；已授权对象可以逐项、批量撤销，也可使用“撤销全部显式授权”。
4. 核对“权限来源预览”，点击“保存授权”。撤销草稿也需要保存才生效。
5. 用户刷新工作台，查看并打开获得使用权的应用。

直接授权和组授权取并集：删除直接授权后，用户仍可能通过用户组获得权限。`admin` 默认拥有全部已启用应用的使用权；`netops-config` 可以维护应用授权，但不能因此管理账号或获得所有业务应用的使用权。未启用的应用即使已有授权也不可用。

用户与用户组维护详见[系统管理操作说明](system-management-guide.md)。

## 配置命令与检查连接

1. 打开“设备管理”，左侧“配置命令集”位于“设备资产”上方。在此维护命令，预览适用设备与命令后确认绑定；待启用配置按页面提示“启用并绑定”。
2. 打开“设备资产”，进入目标设备详情，检查管理地址和连接配置。
3. 点击“测试连接”查看结果；点击“连接历史”回查过去的连接状态与失败原因。测试会实际尝试连接设备，应在需要验证连接时执行。
4. 连接失败时，根据显示的原因修正地址、凭据或网络连通性，再测试。

网络巡检页面只使用设备已经绑定的命令集，命令配置维护在设备管理中完成。仅有巡检权限的用户可以选择设备发起巡检，维护资产和命令需另有设备管理应用使用权。

## 新建在线巡检与预检

1. 从“网络巡检”新建在线巡检，或直接打开 [新建在线巡检](http://localhost:3000/apps/inspection?launch=1)。
2. 查看“巡检运行环境”状态，展开“运行环境详情”查看具体检查项。状态无法加载时点击重试；出现阻断时，处理所列问题后点击刷新图标重新检查。
3. 选择指定设备，或使用设备分组、动态分组确定范围。已指定设备时，分组选择会禁用；未指定设备时按分组或当前筛选生成范围，不能假定目标为空。
4. 按需填写工单号，选择是否保存为后续对比基线。
5. 点击底部“预览设备与命令”。这一步生成预检计划，不发起正式巡检。逐台核对设备、管理地址、命令顺序，并查看总数、就绪数、错误数与报告质量。
6. 在预检说明中查看 Parser 覆盖告警、采集器预检问题和规则资产告警；展开设备问题明细查看具体原因。
7. 确认范围和命令后，点击“发起正式巡检”。页面会显示已创建批次及分发状态，随后返回任务列表跟进执行结果。

修改设备范围或执行选项后，需要重新预览。运行环境未就绪、尚未预览或没有就绪设备时不能发起；若仅部分设备就绪，发起结果可能包含跳过设备，应检查页面提示。

Parser 或分析规则覆盖不足可能表现为告警和报告质量降级，并不一定阻止采集。系统会保留原始证据，但“采集成功”不能代替“完整健康结论”；应按预检结果到规则治理补齐对应能力。

## 阅读巡检结果

结果页顶部集中显示巡检结论、依据和可信度，随后展示当前异常、趋势提醒、有效变化。覆盖不足、变化策略缺失和仅有文本证据统一列在“覆盖与结论边界”中，默认可见；“解析覆盖与技术诊断”可展开查看技术细节。

页面使用浅色状态提示与细分隔线，避免连续的大面积告警框。显示方式调整不改变报告判定：“0 项当前异常”仍可能伴随“结论不完整”，不能据此判断全部设备健康。下载报告、查看证据和补齐检查能力入口保持可用；读取失败仍显示错误标识和重试操作。

## 后台查看状态、停用与启用 App

这些命令由服务器维护人员在项目根目录执行。以下是本地 Windows PowerShell 示例，`inspection` 是网络巡检的实际包 ID。

先进入项目目录并查看当前状态：

```powershell
Set-Location -LiteralPath 'C:\Users\wangd\PycharmProjects\PythonProject\netops-agent'
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages status --compact
```

输出包含包 ID、安装版本、状态。需要排查错误或部署检查结果时，去掉 `--compact` 查看详细状态。

停用网络巡检：

```powershell
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages disable inspection
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages status --compact
```

停用会关闭应用准入并执行包自己的任务收尾逻辑，**不是暂停按钮**。网络巡检的待执行任务可能取消或标记失败，重新启用不会自动恢复这些任务。停用前先在任务列表确认在途工作；操作完成后以 `disabled` 状态为准。历史业务数据和授权会保留，但应用停用期间不能正常访问其业务功能。

若状态仍为 `draining`，保留当前部署代码，查看详细状态中的 `error`，处理原因后继续排空：

```powershell
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages drain inspection
```

已有版本未变更、服务均已启动时，重新启用并核验：

```powershell
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages enable inspection --gateway-url http://localhost:8000 --frontend-url http://localhost:3000 --wait-seconds 120
.\venv\Scripts\python.exe web/django_backend/manage.py app_packages status --compact
```

启用会验证 Gateway、生产前端、Worker 和 Beat，最终应显示 `enabled`。验证失败时先按错误修复服务、依赖或部署摘要；不要直接修改数据库状态。若服务未启动，使用既有启动脚本：

```powershell
.\scripts\windows\start.ps1 -EnableApps @('inspection') -NoBrowser
```

设备管理使用包 ID `device-management`，可替换上述命令中的 `inspection`。其它包 ID 以状态列表和对应 `app.yaml` 为准。应用启停影响所有用户，给单个用户取消使用权应在“应用授权”中操作。

升级源码时必须先用旧部署代码完成停用和排空，再替换代码、迁移、构建并启用。完整部署步骤见[能力包安装、升级与启用](capability-packages-guide.md)；本次删除范围见[平台模块清理记录](platform-module-retirement.md)。
