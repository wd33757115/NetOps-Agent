# Phase 1 到 Phase 6 实施状态

本文记录 `netops-platform-evolution-plan-2026-06.md` 中 Phase 1 到 Phase 6 的当前落地状态。

## Phase 1：StateObservation / CurrentState 生产化

已新增平台状态层：

- `src/core/state/domain.py`
- `src/core/state/normalizer.py`
- `src/core/state/repository.py`
- `src/skills/state-normalizer/`

能力：

- 从 PostgreSQL `netops_command_snapshots.structured_json` 生成字段级 `StateObservation`。
- 使用 Parser Template 的 `entity_type`、`primary_keys`、`ignore_fields` 数据契约。
- 生产模式默认更新 `netops_current_state`。
- `parser_validation` 模式禁止写入生产 `CurrentState`。
- Observation 写入具备幂等键；CurrentState 使用稳定 `state_key`。

## Phase 2：Change Detector 切换到状态层

`change-detector` 已增加 `use_state_observations` 模式。

能力：

- 从当前 run 的 `StateObservation` 查找上一条同实体字段状态。
- 支持字段级 `modified`、`added`、`deleted` 变化。
- 状态类字段 `status`、`protocol`、`state` 比较时忽略大小写噪音，例如 `up -> UP` 不再记为变化。
- 生成 `NetworkChange` 时写入：
  - `previous_observation_id`
  - `current_observation_id`
  - `source=state_observation`
  - `old_number`
  - `new_number`
- 保留原有 snapshot/raw hash 对比入口。

当前限制：

- 已避免每条 Observation 单独查询上一状态；当前会批量加载历史 Observation 后按稳定 state key 在内存中匹配。
- 历史 Observation 选择已改为基于 `PatrolRun.started_at` 判断历史 run，而不是直接用 `StateObservation.observed_at <= current_run.started_at`。这样可以兼容离线文件缺少可靠采集时间、`observed_at` 回退为导入时间的场景。
- 后续在超大规模 run 下仍可继续优化为 SQL 窗口函数或临时表匹配，减少历史 Observation 全量加载压力。

## Phase 3：Event Builder 规则版本化

已新增：

- `src/core/patrol/event_rules.py`
- `netops_event_rules`
- `netops_event_rule_versions`

能力：

- 事件判断由规则对象驱动，不再直接散落在 `event_builder.py` 的 if/else 中。
- Event Builder 优先读取 PostgreSQL active 规则；无规则或开发环境不可用时回退内置默认规则。
- 已提供 Parser 管理 API：
  - `GET /api/v1/parser/event-rules`
  - `POST /api/v1/parser/event-rules`
  - `POST /api/v1/parser/event-rules/bootstrap`
- 事件 payload 记录触发规则：
  - `rule_id`
  - `version`
- 首批规则：
  - `CPUHigh`
  - `InterfaceDown`
  - `InterfaceUp`
  - `BGPNeighborLost`
  - `BGPNeighborEstablished`
  - `ConfigChanged`
  - `MemoryHigh`
  - `FanFailed`
  - `PowerFailed`
- 保存事件后回写对应 `NetworkChange.event_status=event_created`。
- 对已评估但未命中任何事件规则的 Change，回写 `NetworkChange.event_status=no_event`，避免事件处理状态长期停留在 `pending`。
- Parser 控制面 UI 已增加“事件规则”页签，可查看规则、初始化默认规则和保存 active 规则版本。

当前限制：

- 规则审批、回滚、灰度发布和规则测试样本尚未实现。

## Phase 4：Device Management 控制面

已完成执行边界收缩：

- 新增 `src/core/credentials/`，由 `CredentialResolver` 统一解析 `credential_ref`。
- `DeviceInventoryManager` 不再直接解析凭证来源。
- 保留现有 PostgreSQL `netops_devices`、`netops_device_groups` 作为设备控制面基础。
- `DeviceFilter` 与 `DeviceRepository.get_devices_by_filter()` 已支持 `device_id`、`vendor`、`family`、`site`、`role`、`status`、`group` 等控制面筛选维度。
- 设备查询结果已返回 `site`、`role`、`groups`，用于 Profile Binding 和 Effective Plan 预览。
- 设备列表不再解析 `credential_ref`；凭证只在 device-patrol / device-backup 真正连接设备前解析，避免资产查询触碰 secret。
- 已新增 Device Management API：
  - `GET /api/v1/devices`
  - `POST /api/v1/devices`
  - `GET /api/v1/devices/{device_id}`
  - `PATCH /api/v1/devices/{device_id}`
  - `POST /api/v1/devices/{device_id}/status`
  - `POST /api/v1/devices/{device_id}/groups`
  - `POST /api/v1/devices/import/preview`
  - `POST /api/v1/devices/import/apply`
  - `POST /api/v1/devices/bulk/preview`
  - `POST /api/v1/devices/bulk/apply`
  - `POST /api/v1/devices/{device_id}/effective-plan`
  - `POST /api/v1/devices/{device_id}/connectivity-test`
  - `POST /api/v1/devices/patrol/preview`
  - `POST /api/v1/devices/patrol/launch`
- 已新增 Django BFF 代理入口 `/api/devices/...`，前端不直接访问 FastAPI。
- 已新增 React `/devices` 页面，支持按设备名、厂商、型号、设备族、站点、角色、分组、状态筛选，展示设备列表、设备详情和巡检 Effective Plan 预览。
- `/devices` 页面已支持新增和编辑设备，维护字段包括设备名、管理 IP、厂商、型号、设备族、OS 版本、序列号、站点、角色、状态、用户名、`credential_ref`、`command_profile`、分组和扩展属性 JSON。
- `/devices` 页面已支持 CSV 文本粘贴与 CSV/XLSX 文件上传式批量导入，先执行预览并标记 `create/update/invalid`，确认后按 `device_name + management_ip` 幂等写入合法设备；导入字段已支持 `os_version` 和 `serial_number` / `serial`。
- `/devices` 页面已支持批量更新，勾选设备时按勾选目标执行，未勾选时按当前筛选条件执行；可批量维护 `command_profile`、状态、站点、角色和分组，分组支持 `replace/append`。
- `/devices` 页面已支持从设备管理发起巡检：勾选设备时按勾选目标执行，未勾选时按当前筛选条件执行；启动前先预览每台设备的 `ExecutionPlan`、命令数量和策略来源，确认后默认按设备启动标准 `patrol-change-event` Workflow，并返回 workflow run id；`direct` 模式仅保留为底层 Celery 调试通道。
- `/devices` 页面已增加设备连通性测试入口，后端复用 `device-patrol` / `device-backup` 的真实 Netmiko 连接路径；测试结果展示 device type、prompt、耗时和失败原因，且仍不返回密码。
- `netops_devices` 已增加最近连接健康字段：`last_connection_status`、`last_connection_checked_at`、`last_connection_failure_code`、`last_connection_error`、`last_connection_latency_ms`；连接测试、巡检/备份真实采集成功或失败、巡检启动前的连接配置预检失败都会回写该状态。
- Device Management 已支持动态设备组，selector 可按 `vendor/model/family/platform/device_type/site/role/group/groups/status/last_connection_status` 等字段组合匹配，批量更新和巡检启动都可直接选择动态组作为目标。
- `/devices` 的 Dynamic Groups 抽屉已增加条件构造器，用户可用“字段 + 多值”方式生成 selector；JSON 编辑框保留为高级入口，并可反向同步到条件构造器。
- `/devices` 已增加 `Bind Profile` 入口，可按勾选设备、动态组或当前筛选条件生成 Profile Binding selector，预览影响设备、Parser Coverage 和阻断命令后保存 active Command Profile Binding。
- `/devices` 页面已展示 Connection 列和设备详情中的最近连接健康状态，便于区分未测试、已连接、连接失败和凭据/IP 前置失败。
- `/devices` 页面已增加最近 `patrol-change-event` Workflow 运行列表，并可直接打开嵌入式运行监控，查看步骤状态、时间线、错误信息和 Langfuse Trace 链接；选中设备后，运行列表会按该设备的 Workflow context `device_id` 过滤。
- Workflow Run 查询已支持 `source`、`status`、`device_id`、`patrol_batch_id` 过滤，可用于 Device Management、巡检历史、批量巡检批次视图和失败重试入口。
- Workflow Run 已新增失败/取消重试接口，Device Management 最近巡检列表可对 `failed` / `cancelled` 的 `patrol-change-event` 运行直接发起重试；重试会复用原始 context、保留 `retry_of_run_id` 和 `parent_run_id`，并继续出现在按设备过滤的运行列表中。
- Device Management 发起一次多设备巡检时会生成统一 `patrol_batch_id`，写入每个 Workflow run 的 context；`/devices` 页面已增加 Patrol Batch History 和 Latest Patrol Batch 区域，按批次展示总数、完成、运行、等待和失败数量，并可在同一批次内打开监控或重试失败 run。
- Workflow API 已提供批次派生视图：`GET /api/v1/workflows/runs/patrol-batches`、`POST /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/retry`、`GET /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/export.csv`；批次重跑只重启 `failed` / `cancelled` 的 run，CSV 导出用于巡检运营留痕。
- Workflow API 已提供巡检批次归档/恢复治理接口：`POST /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/archive`、`POST /api/v1/workflows/runs/patrol-batches/{patrol_batch_id}/restore`；归档状态写入 Workflow Run context 的 `patrol_batch_*` 审计字段，默认批次列表隐藏归档批次，但仍保留报告、CSV 和证据链读取。
- `/devices` 巡检批次历史已增加显示归档开关、治理状态列、归档时间列和归档/恢复操作；已归档批次禁止直接重试或全量重跑，避免把历史批次误当成当前运营任务。
- Device Management 已增加设备级 Evidence Timeline：`GET /api/v1/devices/{device_id}/evidence-timeline` 会将 `patrol-change-event` Workflow Run、`snapshot_run_id`、CommandSnapshot、StateObservation、NetworkChange 和 NetworkEvent 聚合成只读证据链。
- `/devices` 设备详情已增加 Evidence Timeline 抽屉，可查看巡检步骤、快照解析状态、raw hash、状态观测数量、变化和事件，支持从设备视角追溯到原始巡检证据。
- Device 列表已支持可选 `include_patrol_readiness=true`，按当前生产巡检 `ExecutionPlan` 计算 Parser 覆盖率就绪度；`/devices` 页面已展示 `Patrol Ready` 列，区分 `ready`、`blocked`、`error`，并显示命令数与阻断数量。
- Device Management 巡检启动预览已接入生产 Parser Coverage Preflight；`production` 模式下若白名单命令缺少 active 模板或资产校验失败，会在 Launch 前返回 `parser_coverage_not_ready` 和具体阻断命令。
- Device Management 巡检启动预览已接入采集器前置检查：缺少管理 IP、缺少 `credential_ref`、`env:` 凭据无法解析时，会在 Launch 前返回 `collector_preflight_failed` 和具体 `connection_issues`，不再等到 Celery 任务运行时才暴露基础配置错误。
- 设备详情中的 Effective Plan 视图已从调试 JSON 升级为 Profile Binding 镜像视图：展示 `policy_source`、`binding_id`、`profile_version_id`、selector、命令清单、生产 Parser 覆盖率和采集器前置阻断原因。
- `GET /api/v1/devices/{device_id}/effective-plan` 已返回 `parser_coverage` 和 `coverage_summary`，设备详情与巡检启动预览使用一致的 Parser Coverage / Collector Preflight 判断口径。
- 设备仓储已支持按设备 ID 更新资产字段、覆盖分组和设置状态；列表与详情仍统一脱敏，不返回密码。
- 左侧导航已增加 `Devices` 入口，Device Management 不再只藏在 Parser 控制面里。

当前限制：

- Excel 文件上传式导入已实现；后续可继续补充模板下载、列名校验说明和更友好的错误定位。
- Profile Binding 的底层资产仍由 Parser 控制面统一保存；Device Management 已提供设备级镜像视图和批量绑定入口，后续可继续补充审批、回滚、权限和审计留痕。
- 当前 Device Management 巡检启动已默认包装为标准 Workflow run，并已接入最近运行列表、按设备过滤、运行监控、失败重试、批次历史、批次级失败重跑、批次级全量重新运行和批次 CSV 导出；后续仍需补充批次结果报告归档和更细的权限审计。

## Phase 5：Collection Policy / Command Profile 版本化

已新增：

- `src/core/collection/policy.py`
- `netops_collection_policies`
- `netops_command_profiles`
- `netops_command_profile_versions`
- `netops_collection_profile_bindings`

能力：

- `CollectionPolicyResolver` 生成 `ExecutionPlan`。
- `device-patrol` 和 `device-backup` 消费 `ExecutionPlan`，不再自己持有命令选择逻辑。
- `ExecutionPlan.to_metadata()` 已包含 `device_id`、`action`、`mode`、`profile_version_id`、`policy_source`、`binding_id`、`selector` 和 `collection_items`，可作为 Workflow / API 的显式计划输入。
- `device-patrol` Celery 入口已支持可选 `execution_plan` 参数；传入后优先执行该计划，不再基于 filter 临时解析命令策略。
- `patrol-change-event` Workflow 已支持接收 Device Management 生成的 `execution_plan`，并按 `device-patrol -> state-normalizer -> change-detector -> event-builder` 执行标准巡检后处理链路。
- `PatrolTool.patrol_by_plan()` 已支持按显式计划执行单设备巡检，并校验计划的 `device_id`、`action`、`mode` 和命令列表。
- `device-backup` Celery 入口已支持可选 `execution_plan` 参数；`ConfigBackupTool.backup_by_plan()` 与巡检使用同一套计划校验规则。
- 支持从设备属性读取 `patrol_commands`、`backup_commands`。
- 支持优先读取 PostgreSQL active Command Profile。
- 支持携带 `profile_name`、`profile_version_id` 和 collection item 元数据。
- 支持 active Profile Binding：按设备上下文 `vendor/model/family/site/role/group` 等 selector 匹配 active Profile Version，并按 `priority` 选择最优绑定。
- `ExecutionPlan` 已增加 `policy_source`、`binding_id`、`selector`，用于解释“本次巡检为什么使用这套命令”。
- 已提供 Parser 管理 API：
  - `GET /api/v1/parser/command-profiles`
  - `POST /api/v1/parser/command-profiles`
  - `POST /api/v1/parser/command-profiles/{profile_version_id}/activate`
  - `POST /api/v1/parser/command-profiles/{profile_version_id}/deprecate`
  - `POST /api/v1/parser/command-profiles/impact-preview`
  - `GET /api/v1/parser/command-profile-bindings`
  - `POST /api/v1/parser/command-profile-bindings`
  - `POST /api/v1/parser/command-profile-bindings/{binding_id}/enable`
  - `POST /api/v1/parser/command-profile-bindings/{binding_id}/disable`
  - `POST /api/v1/parser/effective-plan-preview`
- Parser 控制面 UI 已增加“采集 Profile”页签，可保存 draft Profile、激活/废弃 Profile Version、创建 Profile Binding、启用/禁用 Binding、查看绑定列表，预览待发布 Profile/Binding 的设备影响范围和 Parser 覆盖率，并预览 `CollectionPolicyResolver` 生成的 ExecutionPlan。
- Effective Plan 预览已展示 `policy_source`、`binding_id`、`profile_version_id` 等来源信息。
- `device-patrol` Skill 元数据已增加 `execution_plan` 输入，用于后续 Workflow 直接传入 Device Management 生成的执行计划。
- `device-backup` Skill 元数据已增加 `execution_plan` 输入，与巡检执行面保持一致。

当前限制：

- Profile draft 保存、显式激活/废弃、Binding 启用/禁用和影响范围预览已实现；仍缺正式审批角色、回滚 UX、删除/清理、审计留痕、权限硬化和完整发布工作流。

## Phase 6：Report / Diff 从 Patrol 拆分

已新增：

- `src/core/patrol/reporting.py`
- `src/skills/patrol-diff/`
- `src/skills/report-generator/`

能力：

- `patrol-diff` 默认 `persist=false`，用于人类报告差异，不污染系统 Change 主链路。
- `report-generator` 从已持久化 Changes / Events 重新生成报告。
- 报告支持 `markdown`、`html`、`xlsx` 三种输出。
- 报告生成可以从历史 run 重放，不影响 Patrol 入库。
- `device-patrol` 核心执行路径已从“巡检报告生成”语义收敛为 raw CLI output collection：`_write_command_report` 已更名为 `_collect_command_outputs`，任务结果新增 `raw_outputs_url` / `raw_outputs_archive`，旧 `download_url` 仅作为兼容字段保留。
- `device-patrol` Skill 输出契约已从 `patrol_report` 调整为 `raw_outputs_archive`，人类报告由下游 `report-generator` 生成。
- Workflow artifact 标准化已识别 `raw_outputs_url` 并生成 `artifacts.raw_outputs_archive`；`WorkflowRunMonitor` 会将 Raw CLI outputs 与 Analysis/Patrol report 分开展示，避免把原始输出归档误认为最终巡检报告。

当前限制：

- 原始 CLI 输出归档仍由 `device-patrol` 上传，用于审计和离线排查；这不是人类报告。后续仍可补充归档保留策略、归档权限审计和批次级报告归档。
- XLSX 当前为轻量标准库实现，后续可替换为更完整的模板化 Excel 渲染。

## Stage 2 进展：DevicePatrolRunner 抽离

已新增：

- `src/core/patrol/runner.py`
- `src/core/backup/runner.py`
- `src/core/execution/`
- `src/core/ingestion/`
- `src/core/ingestion/adapters/cli.py`
- `tests/unit/test_device_patrol_runner.py`
- `tests/unit/test_device_backup_runner.py`
- `tests/unit/test_execution_runner_helpers.py`
- `tests/unit/test_cli_ingestion_adapter.py`

能力：

- `DevicePatrolRunner` 承载巡检执行主流程：
  - 解析显式 `ExecutionPlan`；
  - 调用 `PatrolTool.patrol_by_plan()` 或 `PatrolTool.patrol_by_filter()`；
  - 保存 PostgreSQL snapshot；
  - 归档 raw CLI outputs；
  - 返回 Workflow 需要的 `snapshot_run_id`、`raw_outputs_url`、`raw_outputs_archive`、设备统计和 snapshot 统计。
- `execute_device_patrol_task` 已收敛为薄 Celery 包装层，只负责构造 `DevicePatrolRunRequest`、调用 Runner、记录成功/失败日志。
- `_persist_patrol_snapshots` 兼容导入路径仍保留在 `src.core.celery_tasks.tasks`，实际实现迁移到 `src.core.patrol.runner.persist_patrol_snapshots`，避免现有测试和外部调用立即断裂。
- `DeviceBackupRunner` 已承载配置备份执行主流程：
  - 解析显式 `ExecutionPlan`；
  - 调用 `ConfigBackupTool.backup_by_plan()` 或 `ConfigBackupTool.backup_by_filter()`；
  - 归档配置备份输出；
  - 返回 Skill/Workflow 兼容的 `download_url`、设备统计、输出文件和执行耗时。
- `execute_config_backup_task` 已收敛为薄 Celery 包装层，只负责构造 `DeviceBackupRunRequest`、调用 Runner、记录成功/失败日志。
- `src/core/execution/` 已抽出执行类 Runner 共享能力：
  - `archive_output_files()` 统一 zip 归档、MinIO 上传和预签名 URL 生成；
  - `ArchiveSpec` 统一归档路径、zip 命名和日志事件前缀；
  - `runner_success_payload()` 统一 Runner 返回字段，保留 Skill/Workflow 兼容 payload。
  - `load_inventory_execution_context()` 统一加载 netops 工具、DB manager、显式 `ExecutionPlan` 和 `DeviceFilter`；
  - `run_plan_or_filter()` 统一按显式 plan 优先、否则按 filter 执行异步工具方法。
- `CLICommandCollector` 已成为 CLI 命令采集的第一版 Ingestion Adapter：
  - 统一 Netmiko 设备类型推断；
  - 统一 `credential_ref` 解析入口；
  - 统一 command-report 文件格式；
  - 统一连接测试路径。
- `PatrolTool` 和 `ConfigBackupTool` 已支持注入 `cli_collector`，默认使用 `CLICommandCollector`，测试和未来 Adapter 替换可以不再 monkeypatch 私有采集函数。

验证：

```powershell
venv\Scripts\python.exe -m pytest tests/unit/test_execution_runner_helpers.py tests/unit/test_device_backup_runner.py tests/unit/test_device_patrol_runner.py tests/unit/test_cli_ingestion_adapter.py tests/unit/test_collection_policy.py tests/unit/test_patrol_change_event_pipeline.py -q
```

结果：

```text
39 passed
```

当前限制：

- CLI 连接与命令执行已进入 `CLICommandCollector`，但尚未抽象出长期连接池、并发限速、命令级超时策略和连接会话复用。
- `device-patrol` 和 `device-backup` 已分别具备 Runner，且归档、结果 payload、工具加载和 plan/filter 分支执行已抽为共享 helper；后续可继续收敛命令执行错误分类、连接池、并发限速和命令级超时策略。

## 数据库迁移

已新增：

- `migrations/versions/0011_event_rules_and_collection_profiles.py`

新增控制面表：

- `netops_event_rules`
- `netops_event_rule_versions`
- `netops_collection_policies`
- `netops_command_profiles`
- `netops_command_profile_versions`
- `netops_collection_profile_bindings`

## 验证

### 两季度全链路验收

已新增可重复验收脚本：

```text
scripts/test/phase1_6_e2e_validation.py
```

脚本默认使用：

- 上一季度数据：`dev_test1`
- 当前季度数据：`dev_test`
- 输出目录：`.runtime/test-reports`

执行链路：

```text
preview_raw_path
  -> import_raw_path(parser_validation)
  -> state-normalizer(update_current_state=false)
  -> change-detector(use_state_observations=true)
  -> event-builder(persist=true, publish=false)
  -> report-generator(markdown)
```

执行命令：

```powershell
venv\Scripts\python.exe scripts\test\phase1_6_e2e_validation.py
```

最新结果：

```json
{
  "previous_run_id": "phase1-6-validation-previous-20260622_135152",
  "current_run_id": "phase1-6-validation-current-20260622_135152",
  "previous_observations": 11944,
  "current_observations": 11966,
  "change_count": 148,
  "event_count": 0,
  "no_event_change_count": 148,
  "summary_markdown_path": ".runtime/test-reports/phase1_6_e2e_summary_20260622_135152.md"
}
```

数据覆盖：

- 两个季度均扫描 27 个文件，共 54 个文件。
- 上一季度导入 462 条命令，当前季度导入 531 条命令。
- 上一季度快照 parse 状态：
  - `parsed`: 99
  - `parse_empty`: 4
  - `template_missing`: 141
  - `parse_failed`: 5
  - `ignored`: 212
- 当前季度快照 parse 状态：
  - `parsed`: 98
  - `parse_empty`: 4
  - `template_missing`: 143
  - `parse_failed`: 5
  - `ignored`: 272

本次验收修复的问题：

- 初次验收中 `compared_observations=0`，原因是历史 Observation 查询使用了 `observed_at <= current_run.started_at`，离线数据缺采集时间时会误过滤上一季度数据。
- 修复后 `compared_observations=12085`，能够真实做跨季度状态比较。
- 初次验收中存在大量 `up -> UP`、`down -> DOWN` 噪音变化；已加入状态类字段大小写归一比较，Change 数从 2252 收敛到 2130。

当前验收结论：

- `CommandSnapshot -> StateObservation -> NetworkChange -> report-generator` 已能基于两季度真实目录跑通。
- `change-detector` Observation 模式已避免把当前 run 未采集命令对应的历史 observation 判定为 deleted，也避免把上一轮未采集集合中的当前 observation 判定为 added；本轮真实验收变化量从 2196 收敛到 148。
- Phase 1-6 验收脚本显式使用 previous/current run 做 observation 对比，不再被旧验证 run 污染基线。
- `StateNormalizer` 已补充默认状态语义治理：`uptime` 不进入状态事实，`description` 的空占位值规范化为空字符串。
- Phase 1-6 验收报告已增加 Parser Coverage Gaps，按命令、型号和厂商聚合 `template_missing` 与 `parse_failed`，当前两季度结构化覆盖率约为 41%。
- `event-builder` 在本次真实数据中输出 0 个事件。根据 no-event 诊断，当前变化主要是 CPU 小幅波动、少量接口 IP/speed/description 变化、内存 free/used 和温度 reading 变化；没有命中现有默认事件规则中的 `CPUHigh`、`MemoryHigh`、`InterfaceDown`、`BGPNeighborLost` 等事件级条件。
- 148 条 Change 已被事件规则评估并标记为 `no_event`，说明事件处理状态闭环可审计。
- 这说明事件链路可执行，但事件规则覆盖率和 Parser 数据质量仍需继续提升，不能把 Phase 3 判定为生产完成。

### 浏览器真实验证

已在本地浏览器访问：

```text
http://127.0.0.1:3000/skills
```

并进入 `skills-Parser 控制面` 页面执行真实联调：

- React 前端调用 Django BFF。
- Django BFF 转发到 FastAPI Parser API。
- FastAPI 读写 PostgreSQL 控制面表。

验证过程中发现并修复：

- `POST /api/parser/event-rules/bootstrap/` 被 Django BFF 返回 404。
- 原因是 BFF 只代理了固定层级 Parser 路由，没有覆盖 `event-rules/bootstrap` 这类多段路径。
- 已新增 BFF catch-all Parser 代理路由：
  - `web/django_backend/bff/views/parser.py`
  - `web/django_backend/bff/urls.py`

已验证事件规则页签：

- 点击 `初始化默认规则` 后成功写入 PostgreSQL。
- UI 显示 `事件规则 (9)`。
- 默认 active 规则包括：
  - `bgp-neighbor-established`
  - `bgp-neighbor-lost`
  - `config-changed`
  - `cpu-high`
  - `fan-failed`
  - `interface-down`
  - `interface-up`
  - `power-failed`

已验证采集 Profile 页签：

- 创建测试 Profile：`browser-test-patrol`。
- 命令：
  - `show version`
  - `show inventory`
  - `show interface status`
- 初次验证时发现 UI 表格读取字段错误，后端返回 `profile_name/items/version_status`，前端曾按 `name/action/commands` 渲染，导致动作为空、命令数为 0。
- 已修复：
  - `web/react_frontend/src/components/skills/ParserControlTab.tsx`
- 修复后 UI 正确显示：
  - Profile：`browser-test-patrol`
  - 动作：`patrol`
  - 版本：`1`
  - 状态：`active`
  - 命令数：`3`
  - 命令列表：`show version | show inventory | show interface status`

已验证 Effective Plan 预览：

输入设备上下文：

```json
{
  "device_id": "browser-device-1",
  "vendor": "Cisco",
  "command_file": "browser-test-patrol",
  "attributes": {}
}
```

结果：

- UI 返回 `执行计划：3 条命令`。
- 返回内容包含 `profile_name=browser-test-patrol`。
- 返回内容包含 `profile_version_id`。
- 返回 collection items 与 3 条命令一致。

说明：

- `browser-test-patrol` 是浏览器联调产生的本地验证数据，可在后续清理或保留为 UI 验证样例。
- 该验证只证明 Parser 控制面 UI、BFF、FastAPI、PostgreSQL 的事件规则和采集 Profile 接入可用；不等同于两季度真实巡检全链路验收完成。

已执行：

```powershell
venv\Scripts\python.exe -m pytest tests/unit/test_parser_management_api.py tests/unit/test_event_rules.py tests/unit/test_collection_policy.py tests/unit/test_state_normalizer.py tests/unit/test_patrol_reporting.py tests/unit/test_patrol_change_event_pipeline.py -q
```

结果：

```text
24 passed
```

状态 Observation 批量匹配优化后再次执行同一组测试：

```text
24 passed
```

已执行：

```powershell
venv\Scripts\python.exe -m compileall src\core\state src\core\collection src\core\credentials src\core\patrol src\skills\state-normalizer src\skills\patrol-diff src\skills\report-generator migrations\versions\0011_event_rules_and_collection_profiles.py
```

结果：通过。

已执行 migration 静态导入检查：

```powershell
venv\Scripts\python.exe -c "import importlib.util; p='migrations/versions/0011_event_rules_and_collection_profiles.py'; spec=importlib.util.spec_from_file_location('m', p); m=importlib.util.module_from_spec(spec); spec.loader.exec_module(m); print(m.revision, m.down_revision)"
```

结果：

```text
0011_rules_profiles 0010_state_observation
```

已执行前端构建：

```powershell
npm run build
```

结果：通过。Vite 输出了既有的 chunk size 与动态/静态混合导入警告，但没有编译错误。

新增 Parser 控制面 UI 后再次执行：

```powershell
npm run build
```

结果：通过。仍只有既有 chunk size 与动态/静态混合导入警告。

两季度全链路验收脚本新增后再次执行：

```powershell
venv\Scripts\python.exe -m pytest tests/unit/test_parser_management_api.py tests/unit/test_event_rules.py tests/unit/test_collection_policy.py tests/unit/test_state_normalizer.py tests/unit/test_patrol_reporting.py tests/unit/test_patrol_change_event_pipeline.py -q
```

结果：

```text
26 passed
```

再次执行：

```powershell
npm run build
```

结果：通过。仍只有既有 chunk size 与动态/静态混合导入警告。

## Stage 3 进展：RawEvidence 元数据层

已新增：

- `src/core/evidence/`
- `migrations/versions/0018_raw_evidence_layer.py`
- `tests/unit/test_raw_evidence.py`

能力：

- 新增 `netops_raw_evidence`，作为多数据源原始证据元数据表。
- 当前 `CommandSnapshot` 保留为 CLI 专用快照表，同时被映射为 `RawEvidence(source_type=cli, evidence_type=cli_command_output)`。
- `PatrolRepository.save_snapshot_idempotent()` 在保存或幂等命中 snapshot 时同步 upsert RawEvidence 元数据。
- `StateObservation` 已携带 `source_type`、`evidence_id`、`schema_id`，状态事实可以追溯到统一 Evidence。
- `CurrentState` 已携带 `source_type`、`source_evidence_id`、`schema_id`，当前状态视图也保留来源证据锚点。
- `StateRepository.get_snapshot_context()` 读取旧 snapshot 时会自动补齐 CLI RawEvidence 元数据，避免迁移前数据在归一化时出现证据外键缺失。
- Device Evidence Timeline 的 snapshot 项已返回 `evidence_id`、`source_type`、`evidence_type`、`content_ref`，旧 snapshot 没有 RawEvidence 行时会使用确定性兼容 ID。

已执行数据库迁移：

```powershell
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m alembic current
```

结果：

```text
0018_raw_evidence (head)
```

已执行测试：

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_raw_evidence.py tests\unit\test_parser_control_plane.py tests\unit\test_state_normalizer.py tests\unit\test_patrol_change_event_pipeline.py -q
```

结果：

```text
32 passed
```

再次执行 Stage 2 runner / adapter 回归：

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_execution_runner_helpers.py tests\unit\test_device_backup_runner.py tests\unit\test_device_patrol_runner.py tests\unit\test_cli_ingestion_adapter.py tests\unit\test_collection_policy.py tests\unit\test_patrol_change_event_pipeline.py -q
```

结果：

```text
39 passed
```

已执行编译检查：

```powershell
venv\Scripts\python.exe -m compileall src\core\evidence src\core\patrol\repository.py src\core\state src\gateway\devices_api.py src\infrastructure\db\models.py migrations\versions\0018_raw_evidence_layer.py
```

结果：通过。

当前限制：

- RawEvidence 首版只覆盖 CLI command output 元数据，不复制原始 CLI 文本。
- gNMI/Syslog/SNMP 证据写入 Adapter 尚未实现。
- Change/Event 返回体尚未直接展开 Evidence Pack，只能经 Observation/Snapshot 继续追溯。

## 下一步建议

1. 提升模板覆盖率，优先处理两季度验收中 `template_missing` 和 `parse_failed` 的高价值命令。
2. 优化 Parser 数据契约，修复接口表头被解析成实体、接口状态格式不统一等问题。
3. 增强 Event Rule 覆盖率与规则测试样本，明确哪些 `deleted`、接口状态和 CPU 波动应升格为事件。
4. 补全 Device Management 的平台、设备类型、动态分组模型、凭据绑定 UI、连接健康历史明细和健康状态筛选。
5. 完成 Command Profile 正式审批发布、回滚 UX、删除/清理、审计留痕和权限硬化。
6. 将旧 Patrol 报表路径彻底收敛到 `patrol-diff` 和 `report-generator`。
## 2026-06-27 Device Management 增量

- Device Management API 新增 `DELETE /api/v1/devices/{device_id}`，采用软删除语义：设备状态标记为 `deleted`，历史巡检、Workflow、RawEvidence 和审计链路不被物理破坏。
- 设备管理 UI 新增行内删除按钮，删除前弹出确认，删除成功后刷新设备列表并清理当前选择状态。
- 设备新增/编辑表单新增“巡检命令”多行输入，保存时写入 `attributes.patrol_commands`；高级 `attributes_json` 继续保留用于其它扩展属性。
- 批量更新表单新增“批量设置巡检命令”，后端 `DeviceBulkUpdate` 支持 `patrol_commands`，按设备现有 attributes 合并写入，不覆盖无关扩展属性。
- 项目依赖补充 `netmiko>=4.7.0,<5.0.0`，否则真实设备连接路径无法运行。
- GNS3 验证：`10.10.1.1`、`10.10.1.2` SSH 22 端口可达；使用项目 `test_device_connectivity()` 按 `vendor=H3C` 推断 `hp_comware` 成功登录，prompt 均为 `<H3C>`。
- GNS3 设备资产已写入 PostgreSQL：`GNS3-H3C-1`、`GNS3-H3C-2`，凭据只保存为 `env:GNS3_NETOPS_PASSWORD` 引用，未保存明文密码；默认巡检命令为 `display version`、`display interface brief`。

## 2026-06-27 ExecutionPlan 契约收敛

- `ExecutionPlan` 新增 `credential_ref` 与 `collector_options`，并纳入 `to_metadata()` / `execution_plan_from_metadata()`，使 Workflow、API、Runner 和审计视角都能解释本次采集使用的凭据引用与采集器参数。
- `CollectionPolicyResolver` 会从设备资产生成计划时带入 `credential_ref`，并按 `collector_options` + `{action}_collector_options` 合并采集器参数；动作级参数优先于通用参数。
- CLI Adapter 新增执行计划上下文合并，不修改原始设备资产；采集时使用计划内 `credential_ref`、`netmiko_device_type`、`read_timeout`、`port`、`conn_timeout`、`auth_timeout`、`banner_timeout` 和 `global_delay_factor`。
- 前端 API 类型同步补充 `credential_ref` 与 `collector_options`，后续 Device Management / Patrol Ready / Effective Plan UI 可直接展示计划级采集上下文。
- Device Management 设备详情的巡检计划预览已展示 `credential_ref` 和 `collector_options`；Parser 控制面的执行计划预览也展示同一组计划上下文，便于从 UI 解释“本次为什么这样连接和采集”。

## 2026-06-27 ExecutionPlan 持久化与复放

- 新增 `netops_collection_execution_plans` 控制面表和迁移 `0020_collection_execution_plans`，保存每次巡检启动前的不可变计划 JSON、稳定 `plan_sha256`、命令数、Profile/Binding 来源、`credential_ref`、`collector_options`、启动来源、批次、Workflow/Celery 关联和状态。
- 新增 `ExecutionPlanRepository`，由平台层负责创建计划记录、标记 launched / launch_failed，并支持按 `execution_plan_id` 读取计划 JSON；Skill 不直接操作该表。
- Device Management 巡检启动时不再只把临时 JSON 推给 Workflow/Celery，而是先持久化 ExecutionPlan，再把 `execution_plan_id` 与原计划一起传入上下文；启动结果也返回每台设备的 `execution_plan_id`。
- `DevicePatrolRunner` 支持在没有完整 `execution_plan`、但传入 `execution_plan_id` 时从平台 Repository 读取不可变计划，实现后续 Workflow 只传 ID 的复放路径。
- device-patrol snapshot 入库 metadata 增加 `execution_plan_id`，使“计划记录 -> Workflow/Celery -> snapshot -> evidence”可以互相追溯。

## 2026-06-27 ExecutionPlan 控制面可见性

- 新增 `GET /api/v1/devices/{device_id}/execution-plans`，按设备查询最近 ExecutionPlan 记录，返回计划 ID、状态、Profile/Binding 来源、命令数、`plan_sha256`、Workflow/Celery 关联、`credential_ref` 和 `collector_options`。
- Device Evidence Timeline 现在会把 Workflow 上下文中的 `execution_plan_id` 解析为执行计划摘要；即使通过 `workflow_run_id` 回查，也能把计划记录挂回证据链。
- `/devices` 证据链抽屉顶部新增“执行计划记录”表，展示最近计划、计划哈希和启动关联；Workflow 展开明细中也显示关联的 `execution_plan_id`、计划来源和命令数。
- 前端 API 类型新增 `DeviceExecutionPlanRecord` / `DeviceExecutionPlanResponse`，后续可以继续扩展为计划 diff、复放和审计详情页面。
