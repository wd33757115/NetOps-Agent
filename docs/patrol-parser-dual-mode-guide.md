# 巡检执行与规则制作的数据边界

核对日期：2026-09-06。两条业务路径共享上传、设备身份和证据基础设施，但用途及发布权限不同。

## 工作台入口

- “网络巡检” `/apps/patrol`：在线采集或离线导入，运行已配置的检查项，查看结果、缺口、变化和报告。
- “巡检规则生成”：配置人员准备样本、人工定义检查项和命令关联，再自动制作、验证和授权发布。具体入口由 App Registry 管理，不再以 `/devices` 或旧 Skill 深链接作为业务导航。

设备管理和规则配置受同一配置能力控制，普通用户可使用的巡检操作以服务端权限为准。详见[权限指南](permissions-guide.md)。

## 用途与数据

| 路径 | 数据用途 | 允许的后续动作 |
| --- | --- | --- |
| Parser 样本验证 | 验证新设备、新版本和新格式 | 创建与修复候选，不能把验证结果当作正式当前状态 |
| 规则历史回放 | 在明确周期和设备范围中验证规则 | 产生回放证据，不能借此发布正式事件 |
| 正式在线巡检 | 当前设备采集与检查 | 使用已激活且符合范围的运行资产，形成具有相应用途资格的结果 |
| 离线历史巡检 | 历史 CLI 的单周期检查或双周期比较 | 保留历史采集时间与用途，不声称当前设备状态 |

Snapshot、Dataset、任务与事实保存在 PostgreSQL。文件及报告使用对象存储；对象键、所有权、用途和摘要共同约束访问。旧 SQLite 文件不是在线运行数据源。

## 样本准备与执行

上传后先预览文件、设备、命令和周期，确认设备绑定与检查项关联，再执行制作。预览不应被当成已完成采集或验证。解析样本中的缺模板、解析失败是待补能力；它们不是健康结果。

双周期比较需要真实采集时间，baseline 必须早于 current，并且具有可比较的设备和标准口径。历史 Workflow 的状态标准化使用 `update_current_state=false`，默认不发布事件。必要资产、设备身份或时间证据不满足时，保留阻断原因。

重试通过任务 attempt 和源证据标识追踪，不直接覆盖上一轮审计。文件身份、哈希与任务作用域需一致；无法确认相同证据时不能仅靠同名文件去重。

## 巡检发现缺口以后

正式巡检只消费当前可用资产，不在运行中临时调用模型补规则。缺失模板或解析失败可以写入缺口台账；启用 `PRODUCTION_GAP_LEDGER_ENABLED` 时，Beat 可将达到条件的缺口提升为 `production_gap` 草稿任务，仍不自动 dispatch。

配置人员在规则工作区处理缺口，人工确定检查项及命令关联，再进入自动制作；完成后重新巡检。此时是新的任务和证据，不改写旧报告为成功。

## 模型上下文

Parser 生成需要脱敏 CLI；标准规则生成只使用检查项语义。旧规则资产 Workflow 的不可变 Dataset Context 使用脱敏结构化记录，不能把这三种上下文混为“模型完全不读样本”或“模型可读所有原文”。详见[TextFSM 指南](parser-generation-guide.md)与[规则架构](patrol-rule-architecture.md)。

## 实现与验证

- [离线任务](../src/core/patrol/offline_jobs.py)、[后台执行](../src/core/patrol/offline_tasks.py)
- [缺口台账](../src/core/parser/gap_ledger.py)、[历史巡检 Workflow](../src/workflows/network/patrol-history-change-event/WORKFLOW.yaml)
- [报告用途](patrol-report-guide.md)、[当前业务操作](patrol-workflow-refactor-guide-2026-09-05.md)

接口以当前 OpenAPI 为准。两种任务分别使用 asset-source-jobs 与 patrol/offline-jobs 服务端入口，不能只改变 URL 就跨用途读取或执行任务。测试和回放应使用隔离数据，见[维护指南](maintenance.md)。
