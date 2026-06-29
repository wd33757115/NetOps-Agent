# Device Management 与 device-patrol 优先演进计划

本文记录当前阶段的优先级调整：先完善 Device Management 控制面和 device-patrol 执行面边界，再继续扩展 Parser、Change、Event、Graph 与 RCA。

## 核心判断

device-patrol 不应继续变成“超级 Skill”。它的职责应收敛为：

- 接收 Device Management 解析出的执行计划；
- 解析短生命周期凭证；
- 连接设备并执行命令；
- 保存原始巡检证据与解析状态；
- 不负责设备分组、命令策略选择、模板生成、事件构建或 RCA。

Device Management 则应成为统一控制面：

- 管理设备资产、站点、角色、厂商、型号、设备族、分组和标签；
- 管理 credential_ref 绑定，而不是保存明文密码；
- 管理 Command Profile、Profile Version 和 Profile Binding；
- 提供巡检前 Effective Plan 预览；
- 提供 Parser Coverage Preflight，阻止生产巡检使用未准备好的结构化命令。

## 本次已落地

### 1. 设备筛选维度扩展

`DeviceFilter` 和 `DeviceRepository.get_devices_by_filter()` 已支持以下控制面维度：

- `device_id`
- `device_name`
- `ip`
- `group`
- `vendor`
- `model`
- `family`
- `site`
- `role`
- `status`

设备查询结果返回 `site`、`role`、`groups`，为后续 Profile Binding 和执行计划预览提供上下文。

### 2. 凭证解析边界收敛

设备列表与设备筛选不再解析 `credential_ref`。  
凭证只在真正连接设备时由执行面解析：

```text
Device Management 返回 credential_ref
        ↓
device-patrol 连接设备前解析
        ↓
Netmiko ConnectHandler
```

这样可以避免“查看设备列表”时意外触碰 secret，也为后续 Vault / Secret Manager / 审计留出边界。

### 3. Profile Binding 解析

`CollectionPolicyResolver` 已支持 active Profile Binding：

```text
设备上下文
  ↓
selector_json 匹配 vendor / model / family / site / role / group
  ↓
优先级 priority 选择 Profile Version
  ↓
生成 ExecutionPlan
```

执行计划新增元数据：

- `policy_source`
- `binding_id`
- `selector`

这样 UI、日志和巡检报告都能解释“为什么这台设备用了这套命令”。

### 4. 管理 API

新增 Parser 管理 API：

- `GET /api/v1/parser/command-profile-bindings`
- `POST /api/v1/parser/command-profile-bindings`

前端 API 类型已同步，后续可以在 Parser 控制面或 Device Management 页面增加绑定管理 UI。

## 优先落地阶段

### Phase A：Device Management MVP

目标：让设备资产能支撑稳定的巡检计划。

任务：

- 补齐设备资产字段：站点、区域、角色、厂商、型号、设备族、平台、序列号、状态、标签；
- 增加设备导入与批量编辑能力；
- 设备详情页展示当前 Profile、Credential Ref、最近巡检、最近解析状态；
- 增加 Profile Binding UI，支持按 `vendor/model/family/site/role/group` 创建绑定；
- 增加 Effective Plan Preview 页面，展示命令来源、绑定规则和 Parser 覆盖率。

验收：

- 一个设备无需在自身属性里写命令，也能通过 Binding 得到巡检计划；
- 用户能在 UI 中解释计划来源；
- 列设备不会解析或暴露密码。

### Phase B：device-patrol 执行面收敛

目标：巡检 Skill 只执行计划，不再拥有策略。

任务：

- device-patrol 输入改为优先接收 `execution_plan_id` 或完整 `ExecutionPlan`；
- 保留按 filter 启动，但 filter 必须先经过 Device Management 解析为计划；
- 巡检输出写入 PostgreSQL 快照表，并记录：
  - `inventory_device_id`
  - `profile_version_id`
  - `collection_item_id`
  - `credential_ref`
  - `parser_template_id`
  - `parse_status`
- 生产模式必须通过 Parser Coverage Preflight；
- parser_validation 模式只写测试数据，不更新生产 CurrentState。

验收：

- device-patrol 不再自行决定默认命令；
- 同一设备同一 Profile Version 的巡检结果可追溯；
- 生产巡检遇到应结构化但无 active 模板的命令会被预检阻断。

### Phase C：巡检证据与可追溯性

目标：把巡检从“输出文件”变成可审计证据链。

任务：

- 原始输出保存为 evidence object 或受控文件引用；
- PostgreSQL 保存 snapshot metadata 与 raw hash；
- 报告、Change、Event 都能反查到 snapshot 与原始证据；
- 设备详情页展示历史巡检时间线。

验收：

- 任一事件可以回溯到字段变化、StateObservation、CommandSnapshot 和 raw evidence；
- 重跑报告不会重新巡检设备。

### Phase D：生产策略治理

目标：让 Profile 与 Parser 资产发布可控。

任务：

- Command Profile 增加 draft/review/active/deprecated 状态流；
- Profile Binding 支持发布、禁用、优先级调整和影响范围预览；
- Parser Coverage Preflight 对新 Profile 发布做阻断或警告；
- 增加变更审计与回滚入口。

验收：

- 新 Profile 激活前能看到影响设备数、命令数、Parser 覆盖率；
- 回滚 Profile 不影响历史巡检证据；
- 生产巡检始终使用 active Profile Version。

## 暂缓内容

以下能力不进入当前优先阶段：

- RCA Agent；
- Temporal Knowledge Graph；
- 大规模 gNMI / Telemetry；
- 自动修复生产模板；
- 巡检同步触发 LLM 生成模板。

这些能力依赖稳定的设备资产、执行计划、Parser 合约和状态层。先把控制面和执行面分干净，后续的智能化才不会被基础数据拖住。
