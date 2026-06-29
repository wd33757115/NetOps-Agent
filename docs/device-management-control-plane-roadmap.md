# Device Management 控制面产品化路线图

## 目标定位

Device Management 不再只是设备 CRUD 页面，而是网络自动化控制面。它负责把设备资产、采集策略、Parser 覆盖率、巡检执行、变化与事件治理放在同一个产品入口下。

核心原则：

- Device Management 管设备资产和执行上下文。
- Parser 控制面管结构化能力和模板资产。
- Skill 之间不直接调用，通过 Workflow、数据库状态和领域事件组合。
- Phase 1 到 Phase 6 都按可插拔 Agent 模块建设，每个模块独立声明输入、输出、状态、审计和启停策略。

## 当前已落地

- `/devices` 页面升级为控制面式页签。
- `总览与设备资产` 提供设备、凭据、连接、巡检就绪和运行任务统计。
- `Parser 控制面` 已整合到 Device Management 入口。
- `Agent 模块` 页签展示 Phase 1 到 Phase 6 的模块边界。
- 前端已新增模块注册表 `web/react_frontend/src/components/devices/agentModules.ts`，每个模块声明输入、输出、依赖、控制面入口和健康信号。
- 后端已新增 `GET /api/v1/devices/agent-modules/health`，从 PostgreSQL 控制面读取模板、任务、状态、变化、事件、设备、Profile 和巡检步骤的真实运行态。
- `Agent 模块` 页签已接入运行态 API，每 10 秒刷新一次，并把静态模块注册表与真实健康状态合并展示。
- 模块注册表已增加 `healthChecks`，每个 Phase 模块至少声明 3 个可验证健康检查；UI 同时展示设计态检查项和后端运行态检查结果。
- Agent 模块健康接口已返回运行证据 `evidence`，包括最近激活模板、失败任务、状态观测、网络变化、网络事件、连接异常和 Workflow 步骤证据。
- `Agent 模块` 页签已展示最近运行证据，并在展开行中提供对象 ID、时间、摘要、详情和控制面跳转入口，便于从健康状态追溯到真实运行记录。
- 后端已新增 `netops_agent_module_controls` 控制表和 `PATCH /api/v1/devices/agent-modules/{module_id}/control`，支持模块启用、停用和维护模式。
- `Agent 模块` 页签已增加治理操作，停用或维护时必须填写原因，可记录维护窗口、更新人和审计日志，运行健康与人工治理状态分开展示。
- 巡检预览与启动已接入模块治理状态；`parser-control`、`state-normalizer`、`change-detector`、`event-builder` 等关键模块处于停用或维护时，巡检 Workflow 会在预检阶段被阻断并返回模块原因。
- Workflow 引擎启动入口已接入同一套 Agent 模块治理规则；直接调用 Workflow API 或重试/重跑 `patrol-change-event` 时，也会在创建 WorkflowRun 前被阻断。
- `AgentModuleControlTab` 和 `ControlPlaneOverview` 已从 `DevicesPage` 中拆出，后续模块治理可以继续独立演进。
- `PatrolOperationsPanel` 和 `DeviceAssetInventoryPanel` 已从 `DevicesPage` 中拆出，巡检执行区和资产筛选区具备独立演进边界。
- `PatrolOperationsPanel` 已补充批次完成率、失败/取消运行定位表、直接查看/重试入口、批次结果摘要和 CSV/Markdown 交付物入口，巡检批次不再只是历史列表，而是可操作、可交付的运行控制台。
- 巡检批次运行、样本事件和样本变化已接入设备级证据链入口，可从批次摘要直接打开对应设备的 Evidence Timeline。
- 发起巡检弹窗已补充结构化 Profile Preflight 明细，按模块治理阻断、Parser 覆盖阻断和采集器预检问题分开展示，并在设备计划表中显示预检状态与问题计数。
- 巡检批次历史已补充归档/恢复治理能力；归档仅写入 `patrol_batch_*` 上下文审计字段，默认从批次列表隐藏，不删除 Workflow Run、Snapshot、Change、Event 或 CSV/Markdown 交付物。

## 模块边界

| 阶段 | 模块 | 职责 | 当前状态 |
|---|---|---|---|
| Foundation | parser-control | 模板资产、任务队列、验证数据集、命令策略、设备身份规则 | 已接入 |
| Phase 1 | state-normalizer | StateObservation / CurrentState 生产化 | 已接入 |
| Phase 2 | change-detector | 基于状态事实生成 NetworkChange | 已接入 |
| Phase 3 | event-builder | 规则版本化 NetworkEvent 构建 | 已接入 |
| Phase 4 | device-management | 设备资产、凭据引用、动态分组、连接健康、执行计划入口 | 建设中 |
| Phase 5 | collection-profile | 采集 Profile 版本、绑定优先级、Parser Coverage Preflight | 建设中 |
| Phase 6 | patrol-diff / report-generator | 报告与人类差异从 Patrol 拆分 | 建设中 |

## 后续实施顺序

1. 继续拆分 `DevicesPage` 中剩余的 Drawer、Modal 和批量动作表单，形成可单独测试的产品级子组件。
2. 为每个 Agent 模块继续补充责任人、维护窗口审批流和更精确的证据详情页入口。
3. 继续完善巡检执行页：权限审计和证据详情深链。
4. 将 change-detector 和 event-builder 的真实事件流接入控制面，不把 backfill 或重解析误报为网络变化。
5. 增加治理页：模板审批、Profile 审批、规则审批、回归验证、审计日志和发布记录。
6. 让 Skills 页面退回 Skill 开发与发布治理，Device Management 承担运行态控制面。

## UI 设计准则

- 默认展示运维人员需要立即判断的状态，而不是系统内部对象列表。
- 表格优先，卡片只用于统计和重复实体，不做装饰性布局。
- 每个阻塞状态必须能点进原因：凭据、连接、Profile、Parser 模板、Workflow 执行。
- 所有自动化动作必须有预览、影响范围、执行结果和审计记录。
- Parser、Patrol、Change、Event 是模块，不是互相嵌套调用的功能按钮。
