# Device Management / device-patrol Update - 2026-06-21

本次优先完善 Device Management 和 device-patrol 的连接健康闭环。

## 已完成

- 新增 PostgreSQL 迁移 `0013_device_conn_health_history`，建立 `netops_device_connection_health` 历史表。
- `DeviceRepository.record_connection_health()` 现在同时更新 `netops_devices` 最新连接状态，并追加不可变历史记录。
- 历史记录包含 `status`、`failure_code`、`error`、`latency_ms`、`source`、`metadata` 和 `checked_at`。
- Device Management API 增加 `GET /api/v1/devices/{device_id}/connection-health`。
- `GET /api/v1/devices`、批量更新过滤、巡检预览和巡检启动过滤均支持 `connection_status`。
- `/devices` 页面增加连接状态筛选、连接健康历史抽屉，并在连接测试后刷新最新状态和历史。
- `device-patrol`、`device-backup`、巡检预检和连通性测试均写入带来源的连接健康记录。

## 验证

```powershell
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q
venv\Scripts\python.exe -m compileall src\gateway\devices_api.py src\core\device_ops\tools.py src\core\device_ops\repository.py src\infrastructure\db\models.py migrations\versions\0013_device_connection_health_history.py
npm run build
```

结果：

- 相关单测 `71 passed`。
- Python 编译检查通过。
- 前端构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。

## 下一步建议

- 补设备平台、设备类型和动态分组模型。
- 做凭据绑定 UI，把 `credential_ref` 从手工文本维护变成可选资产绑定。
- 在设备详情中加入 Profile Binding 镜像视图，让设备、命令策略和巡检计划形成一个操作入口。
- 继续收敛正式巡检只依赖生产模板、测试巡检只进入 parser-validation 数据域的边界。

## 追加增量：资产维度补全

- 新增 PostgreSQL 迁移 `0014_device_asset_dims`，在 `netops_devices` 增加 `platform` 和 `device_type` 字段，并建立索引。
- Device Management API 的新增、编辑、CSV 导入、列表筛选、批量更新、巡检预览和巡检启动过滤均支持 `platform` 与 `device_type`。
- `/devices` 页面增加 Platform 和 Device Type 的列表展示、筛选、详情展示、编辑表单、导入预览和批量更新入口。
- Parser 控制面的 Profile Binding 影响预览设备上下文增加 `platform` 和 `device_type`，可直接作为 selector 维度使用。
- 已执行验证：
  - `venv\Scripts\python.exe -m alembic upgrade head`
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q`，结果 `71 passed`。
  - `venv\Scripts\python.exe -m compileall src\gateway\devices_api.py src\gateway\parser_api.py src\core\device_ops\repository.py src\infrastructure\db\models.py migrations\versions\0014_device_asset_dimensions.py`。
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。

新的下一步建议：

- 实现动态分组规则，把 `platform`、`device_type`、`site`、`role`、`vendor`、`model`、`connection_status` 等字段组合成可复用设备集合。
- 做凭据绑定 UI，把 `credential_ref` 从手工文本维护变成受控资产绑定。
- 在设备详情中加入 Profile Binding 镜像视图和影响预览入口。

## 追加增量：动态设备分组

- 新增 PostgreSQL 迁移 `0015_device_dynamic_groups`，建立 `netops_device_dynamic_groups`。
- 动态组保存 `name`、`description`、`selector_json` 和 `status`，与静态设备组并列存在。
- 动态组 selector 复用 Profile Binding 的匹配语义：大小写不敏感，列表字段支持交集匹配，可使用 `vendor`、`model`、`family`、`platform`、`device_type`、`site`、`role`、`groups`、`last_connection_status` 等设备上下文字段。
- Device Management API 增加动态组列表、创建/更新、启停、预览接口。
- 批量更新和巡检预览/启动支持 `dynamic_group_id`，后端在统一 `_resolve_bulk_devices()` 入口解析目标设备。
- `/devices` 页面增加 Dynamic Groups 抽屉，可创建、编辑、启停和预览动态组；Bulk Update 与 Launch Patrol 弹窗可直接选择动态组作为目标。
- 已执行验证：
  - `venv\Scripts\python.exe -m alembic upgrade head`
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q`，结果 `73 passed`。
  - `venv\Scripts\python.exe -m compileall src\gateway\devices_api.py src\core\device_ops\repository.py src\infrastructure\db\models.py migrations\versions\0015_device_dynamic_groups.py`。
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。

新的下一步建议：

- 做凭据绑定 UI，把 `credential_ref` 从手工文本维护变成受控资产绑定。
- 在设备详情中加入 Profile Binding 镜像视图和影响预览入口。
- 给动态组增加更友好的条件构造器，减少直接编辑 JSON 的门槛。

## 追加增量：凭据引用绑定

- 新增 `CredentialResolver.list_references()` 与 `inspect_reference()`，只枚举符合前缀和敏感字段命名规则的环境变量引用，不暴露任何密钥值。
- Device Management API 增加 `GET /api/v1/devices/credentials` 和 `POST /api/v1/devices/credentials/inspect`，用于列出可绑定凭据引用并检查引用是否可用。
- 批量更新支持 `credential_ref`，可按筛选条件、设备组或动态组统一绑定凭据引用。
- `/devices` 新增/编辑设备弹窗的 `Credential Ref` 改为可输入可选择的 AutoComplete，并提供检查按钮；批量更新弹窗也支持从同一凭据引用列表选择。
- 已执行验证：
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q`，结果 `76 passed`。
  - `venv\Scripts\python.exe -m compileall src\core\credentials src\gateway\devices_api.py`。
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。

新的下一步建议：

- 将凭据引用从环境变量发现升级为 PostgreSQL 凭据资产目录，仍然只保存引用和元数据，不保存明文密钥。
- 在设备详情中加入 Profile Binding 镜像视图和影响预览入口。
- 给动态组增加条件构造器，降低 JSON selector 的使用门槛。

## 追加增量：设备级 Profile Binding 镜像

- `GET /api/v1/devices/{device_id}/effective-plan` 现在返回生产 Parser Coverage 与采集器前置检查摘要：`parser_coverage`、`coverage_summary`、`violations` 和 `connection_issues`。
- `/devices` 设备详情抽屉中的 Effective Plan 视图从原始 JSON 展示升级为设备级 Profile Binding 镜像：
  - 展示策略来源、`binding_id`、`profile_version_id`、Profile、执行动作和模式。
  - 展示 selector，解释当前设备为什么命中这条绑定。
  - 展示命令清单与每条命令的 Parser Coverage 状态。
  - 展示缺模板、资产不可用和凭据/IP 前置阻断，和巡检启动预览使用同一判断口径。
- 本轮同时恢复并启动 Docker Desktop 中的项目 PostgreSQL middleware，执行 `venv\Scripts\python.exe -m alembic upgrade head` 成功。
- 已执行验证：
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py -q`，结果 `36 passed`。
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q`，结果 `76 passed`。
  - `venv\Scripts\python.exe -m compileall src\gateway\devices_api.py`。
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。

新的下一步建议：

- 给动态组增加条件构造器，降低 JSON selector 的使用门槛。
- 增加批量 Profile Binding / Command Profile 绑定入口，形成“设备集合 -> 计划策略”的批量治理流程。
- 做设备级巡检证据时间线，把 Workflow Run、CommandSnapshot、StateObservation、Change 和 Event 串成可追溯视图。

## 追加增量：动态组条件构造器

- `/devices` 的 Dynamic Groups 抽屉新增 Selector Builder。
- 支持按字段选择条件：`vendor`、`model`、`family`、`platform`、`device_type`、`site`、`role`、`group/groups`、`status`、`last_connection_status`。
- 条件值使用 tags 输入，支持一个字段多个候选值；生成结果仍写入同一个 `selector` JSON，不改变后端数据模型。
- 保留 Selector JSON 高级编辑框；手工修改 JSON 后失焦会尝试反向同步到条件构造器。
- 已执行验证：
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py -q`，结果 `36 passed`。
  - `venv\Scripts\python.exe -m compileall src\gateway\devices_api.py src\core\device_ops\repository.py`。

新的下一步建议：

- 增加批量 Profile Binding / Command Profile 绑定入口，形成“设备集合 -> 计划策略”的批量治理流程。
- 做设备级巡检证据时间线，把 Workflow Run、CommandSnapshot、StateObservation、Change 和 Event 串成可追溯视图。
- 补 Excel 文件上传式设备导入，减少粘贴 CSV 的操作成本。

## 追加增量：批量 Profile Binding 入口

- `/devices` 页面新增 `Bind Profile` 抽屉。
- 目标设备集合支持三种来源：
  - 当前勾选设备；
  - 动态设备组；
  - 当前设备列表筛选条件。
- 抽屉会生成并展示实际保存到 Parser 控制面的 selector JSON。
- 用户可选择 active Command Profile Version、设置优先级，并先执行影响预览：
  - 匹配设备数；
  - 阻断设备数；
  - Parser Coverage；
  - 每台设备的 ready / blocked 命令统计；
  - 具体阻断命令。
- 保存时复用 Parser 控制面的 `command-profile-bindings` 接口，Device Management 不复制策略存储逻辑。
- 已执行验证：
  - `npm run build`，构建通过，仅保留既有 Vite chunk size 与动态/静态混合导入警告。
  - `venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py -q`，结果 `44 passed`。

新的下一步建议：

- 做设备级巡检证据时间线，把 Workflow Run、CommandSnapshot、StateObservation、Change 和 Event 串成可追溯视图。
- 补 Excel 文件上传式设备导入，减少粘贴 CSV 的操作成本。
- 为 Profile Binding 增加审批、回滚和审计留痕。

## Additional Increment: Device Evidence Timeline

- Device Management API now exposes `GET /api/v1/devices/{device_id}/evidence-timeline`.
- The timeline links one device to recent `patrol-change-event` Workflow runs, Workflow steps, command snapshots, parser states, state observations, change records, and generated events.
- Snapshot/change/event details are capped for UI readability, while snapshot, observation, change, and event counts are reported as full database counts.
- `/devices` now includes an Evidence Timeline drawer in the device detail view, so patrol evidence can be inspected from the same operational entry point as Effective Plan and connection health.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q
venv\Scripts\python.exe -m compileall src\gateway\devices_api.py
npm run build
```

Result:
- Relevant regression tests passed: `77 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.

## Additional Increment: CSV/XLSX Device Import

- Device Management import API now accepts both `csv` text and base64 encoded `xlsx` content.
- `/devices` Import dialog now supports choosing `.csv` or `.xlsx` files while keeping pasted CSV as a fallback.
- The import contract remains unchanged: first row/header maps to the same device fields, preview marks `create/update/invalid`, and apply writes only valid rows idempotently.
- `openpyxl` is declared as a backend dependency for Excel parsing.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py -q
npm run build
```

Result:
- Device Management API tests passed: `26 passed`.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.

## Additional Increment: Patrol Batch Full Rerun

- Workflow API now exposes `POST /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/rerun`.
- Full rerun creates a new `patrol_batch_id` instead of mutating the original batch, and stores `rerun_of_batch_id` plus `rerun_of_run_id` in each new run context.
- If a device has multiple runs in the source batch, the latest run context is selected, so one device starts only one new run.
- `/devices` Patrol Batch History now provides a `Rerun All` action alongside `Retry Failed` and `Export`.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_workflow_plugins.py -q
venv\Scripts\python.exe -m compileall src\gateway\workflow_api.py
npm run build
```

Result:
- Workflow plugin tests passed: `16 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.

## Additional Increment: Patrol Batch Markdown Report

- Workflow API now exposes `GET /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/report.md`.
- The report follows each batch workflow run to its `snapshot_run_id`, then aggregates persisted `NetworkChange` and `NetworkEvent` records.
- The Markdown report includes workflow status counts, device count, total changes, total events, event type and severity summaries, per-run snapshot/change/event counts, sample events, and sample changes.
- `/devices` Patrol Batch History now separates raw CSV export from a human-readable `Report` download.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_workflow_plugins.py -q
venv\Scripts\python.exe -m compileall src\gateway\workflow_api.py
```

Result:
- Workflow plugin tests passed: `17 passed`.
- Python compile check passed.

## Additional Increment: Device Management Audit Trail

- Device Management write operations now use the shared gateway audit service.
- Audited actions include device upsert, device update, status/group changes, CSV/XLSX import apply, bulk update, dynamic group create/update/status changes, and patrol launch.
- Audit detail records operational metadata such as affected device id, changed fields, target counts, batch id, launch mode, workflow name, and selected device ids.
- Sensitive fields are not written to audit detail; device responses remain redacted.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py -q
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_workflow_plugins.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_skill_execution_result.py -q
venv\Scripts\python.exe -m compileall src\gateway\devices_api.py
```

Result:
- Device Management API tests passed: `27 passed`.
- Relevant regression tests passed: `81 passed`.
- Python compile check passed.

## Additional Increment: Event Rule Test Preview

- Parser Control Plane now exposes `POST /api/v1/parser/event-rules/test`.
- The endpoint evaluates a draft rule condition against user-provided `NetworkChange` samples without saving or activating the rule.
- The response includes sample count, matched count, unmatched count, and per-sample match/error details.
- Parser Control UI event-rule tab now includes a sample JSON editor, a `Test Rule` action, and a match result table.
- This is the first production-hardening step toward event-rule approval: rules can now carry deterministic test evidence before activation.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_parser_management_api.py -q
venv\Scripts\python.exe -m compileall src\gateway\parser_api.py src\core\patrol\event_rules.py
npm run build
```

Result:
- Parser management API tests passed: `9 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.

## Additional Increment: Event Rule Lifecycle Controls

- Event rules now default to draft creation instead of immediate activation.
- Parser Control Plane now exposes `POST /api/v1/parser/event-rules/{version_id}/activate` and `POST /api/v1/parser/event-rules/{version_id}/deprecate`.
- Activating a rule version automatically deprecates the previous active version for the same rule.
- Re-activating an older version provides the rollback path.
- Event Builder remains isolated from drafts because it only loads active rule versions.
- Parser Control UI event-rule table now offers `Activate` and `Deprecate` actions.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_parser_management_api.py tests\unit\test_event_rules.py -q
venv\Scripts\python.exe -m compileall src\gateway\parser_api.py src\core\patrol\event_rules.py
npm run build
```

Result:
- Parser management and event-rule tests passed: `15 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.

## Additional Increment: Production Patrol Admission Gate

- Device Patrol production mode now requires an active command profile version before launch.
- Patrol preview and launch use a separate plan-governance preflight result instead of mixing this with parser coverage or collector credential checks.
- Missing active command profile returns `command_profile_not_ready` with an `active_command_profile_required` plan violation.
- Device readiness and effective-plan preview include `plan_violations` so operators can see why a device is blocked before launching patrol.
- Launch remains delegated to preview, so blocked plans cannot enter workflow or direct Celery execution.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_device_management_api.py tests\unit\test_collection_policy.py tests\unit\test_workflow_plugins.py -q
venv\Scripts\python.exe -m compileall src\gateway\devices_api.py
git diff --check -- src\gateway\devices_api.py tests\unit\test_device_management_api.py
```

Result:
- Device Management, collection policy, and workflow plugin tests passed: `57 passed`.
- Python compile check passed.
- Diff whitespace check passed.

## Additional Increment: Command Profile Rollback Controls

- Command Profile draft version numbers now use the latest historical version, not only the current active version. Multiple drafts for the same profile no longer collide.
- Deprecating an active profile version now updates the profile header to the remaining active version, or marks the profile deprecated when no active version remains.
- Parser Control Plane now exposes `POST /api/v1/parser/command-profiles/{profile_version_id}/rollback`.
- Rollback re-activates a historical profile version and automatically deprecates the previously active version.
- Parser Control UI shows `Rollback` for deprecated profile versions.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_device_management_api.py -q
venv\Scripts\python.exe -m compileall src\core\collection\policy.py src\gateway\parser_api.py
npm run build
git diff --check -- src\core\collection\policy.py src\gateway\parser_api.py web\react_frontend\src\services\api.ts web\react_frontend\src\components\skills\ParserControlTab.tsx tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py
```

Result:
- Collection policy, parser management, and device management tests passed: `55 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.
- Diff whitespace check passed with CRLF warnings only.

## Additional Increment: Command Profile Activation Evidence

- Command Profile impact preview can now persist release evidence onto the profile version `coverage_json`.
- Parser Control UI saves evidence automatically when previewing impact for an existing profile version.
- Activating a profile through Parser Control now requires saved impact evidence.
- Activation is blocked when the latest saved evidence contains blocked devices or blocked commands.
- Internal migration/compatibility paths can still create active profiles directly, but the management API enforces release evidence for operator-driven activation.

Verification:

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py tests\unit\test_device_management_api.py -q
venv\Scripts\python.exe -m compileall src\core\collection\policy.py src\gateway\parser_api.py
npm run build
git diff --check -- src\core\collection\policy.py src\gateway\parser_api.py web\react_frontend\src\services\api.ts web\react_frontend\src\components\skills\ParserControlTab.tsx tests\unit\test_collection_policy.py tests\unit\test_parser_management_api.py docs\device-management-patrol-update-2026-06-21.md
```

Result:
- Collection policy, parser management, and device management tests passed: `56 passed`.
- Python compile check passed.
- Frontend build passed with existing Vite chunk-size and dynamic/static import warnings only.
