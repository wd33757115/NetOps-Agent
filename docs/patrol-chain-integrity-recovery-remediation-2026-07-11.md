# 巡检事实链完整性与失败恢复修复计划

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

更新日期：2026-07-11  
审计基线：`769ac0bacd5c46b69bb3ce7e5dd827195f85c506`

## 1. 项目目标

在上一轮 12 项基础修复之上，补齐巡检链路在部分失败、并发重试、事件复发、
Worker 硬中断和大规模任务下的事实一致性。规则继续由现有 LLM 资产生成、验证、
审批和 active runtime resolver 提供；本轮不新增硬编码业务规则。

必须保持以下 invariant：

1. 进入 production CurrentState 的事实必须有 Observation、Snapshot 和 Evidence。
2. 有效 Collection 发布 CurrentState 后，同 Run 的无关 Collection 失败不能阻断其 Change/Event。
3. Snapshot、Evidence、CollectionResult、Observation 对同一命令使用同一 fact time。
4. Event 的 opening run/change 永不可变；后续命中记录为 EventOccurrence。
5. logical run 与 execution attempt 分离；重试不得把两个 attempt 的事实混入同一 Run。
6. inventory device id 是平台主身份，外部设备名/IP 只作为兼容查询维度。

## 2. 逐项核验

| # | 问题 | 结论 | 修复阶段 |
|---|---|---|---|
| 1 | Normalization 部分成功已更新 CurrentState，但 Workflow 因其他 Snapshot 失败停止 | 存在 | A |
| 2 | 活动 Event 被后续命中改写 origin `run_id` | 存在 | B |
| 3 | command manifest 的 observed_at 未进入 CommandSnapshot | 存在 | A |
| 4 | Tombstone observation id 随机，并发引用可能非 canonical | 存在 | A |
| 5 | Snapshot 查询只匹配 external device id | 存在 | A |
| 6 | 自定义 correlation keys 无法读取实体 dimensions，且分隔符有碰撞 | 存在 | B |
| 7 | Event recurrence 没有一等 occurrence 记录 | 存在 | B |
| 8 | 单设备 lease/task 异常可中断 batch coordinator | 存在 | C |
| 9 | 一次性创建全部设备 asyncio Task | 存在 | D |
| 10 | Worker 硬杀后 Run 可永久 running | 存在 | C |
| 11 | `max_retries` 未形成实际 Celery retry | 存在 | C |
| 12 | `create_run(existing)` 会重开终态 Run | 存在 | C |
| 13 | logical run 与 retry attempt 未分离 | 存在 | C |
| 14 | Streaming persistence 全失败仍可能留下 analysis pending | 存在 | C |
| 15 | 非法 observed_at 静默变成 now | 存在 | A |
| 16 | Redis 首次失败后永久缓存 None | 存在 | C |
| 17 | Redis lease 续租失败静默退化 | 存在 | C |
| 18 | Outbox 整批 lease 无逐项续约 | 存在 | C |
| 19 | EventRule activate 可绕过生成任务审批 | 存在 | D |
| 20 | 每 Change 排序规则、Change 逐行 SELECT/INSERT、Decision 清理在写路径 | 存在 | D |

命令输出已逐条 flush，但 manifest 仅在 finally 写，且 PostgreSQL 仍等设备 callback；
因此“硬杀后命令级 durable fact”也仍存在窗口，纳入 Phase C。

Change Detector 的 persisted shortcut 对整 Run 工作流有效，但 device scope 增量调用会误跳过；
作为 Phase D 的幂等范围修复。

## 3. 分阶段落地方案

### Phase A：事实发布与时间、身份一致性

- `normalize_run` 先构造并验证全部 Snapshot 的 Observation，再一次提交有效 Collection。
- 单 Collection 失败时撤销其 baseline/deletion 资格，但允许其他有效 Collection 继续 Change/Event。
- 全部 Collection 失败时不发布 CurrentState，Run analysis 标记 failed。
- manifest command timestamp 统一传给 Snapshot、Evidence、CollectionResult 和 Observation。
- Tombstone id 使用 namespace UUID + idempotency key 确定性派生。
- Snapshot 查询同时匹配 `inventory_device_id` 和 external `device_id`。
- 非空非法事实时间直接拒绝，不允许伪造成 now。

验收：一个 Snapshot key invalid、另一个正常时，正常 Observation/CurrentState/Change 可继续；
失败 Collection 不可成为 baseline；四层 fact time 相同；并发 tombstone identity 相同。

### Phase B：Event 生命周期、provenance 与 correlation dimensions

- NetworkChange 增加结构化 `dimensions_json`。
- correlation key 使用有序 canonical JSON 的 SHA-256，并保存可解释 dimensions。
- NetworkEvent 保留 immutable opening run/change，增加 latest/first/last/occurrence_count。
- 新增 append-only EventOccurrence，每次命中均记录 run/change/observed_at/severity/payload。
- 活动 Event 再命中只更新 latest/last/count，不修改 opening provenance。
- Evidence Timeline 按 occurrence run 查询事件，历史事件不再跨 Run 移动。

验收：两个 BGP peer 不会因缺失 vrf/peer_ip 被聚合；Run B 复发后 Event 仍属于 Run A，
但 Run B timeline 能通过 occurrence 看到该事件。

### Phase C：失败恢复、租约与命令级 durability

- Collector 使用 `fsync + os.replace` 增量原子 manifest，并在命令完成后触发持久化进度 callback。
- Run 增加 logical id、attempt number、worker、heartbeat、lease、last progress。
- `create_run` 改为 idempotent ensure，不再静默重开终态 Run。
- Celery retry 使用独立 attempt Run id，并只重试平台级瞬态失败。
- 增加 stale PatrolRun reconciler 和 Celery Beat 调度。
- batch 使用固定 worker queue；设备级异常结构化，不终止其他设备。
- persistence 全失败先以 analysis failed 终结 Run，再抛平台错误。
- Redis 失败缓存可退避重连；lease lost 显式记录 degraded 状态。
- Outbox 默认小批 claim，每条 publish 前续约并验证 owner。

验收：kill Worker 后已完成命令有 Snapshot，Run 在 lease 到期后转 abandoned；retry 使用新 attempt；
单设备 lease timeout 不影响其余设备；慢 Outbox publisher 不会继续处理已丢失 owner 的行。

### Phase D：治理与容量

- 普通 EventRule activate 禁止旁路，必须走 pending_approval + validation + approval service。
- 保留显式 break-glass force activate，要求 actor/reason 并写审计日志。
- EventRule 在 load 时排序一次，批内 matcher 不重复排序。
- NetworkChange 使用批量 `INSERT ... ON CONFLICT DO NOTHING RETURNING`。
- suppressed Decision 过期清理由定时任务承担，不在在线写事务中全表 DELETE。
- Change Detector retry shortcut 按 run + device scope 判断。

验收：未审批版本无法普通激活；10 万设备不会创建 10 万 asyncio Task；
大批 Change 无 N 次 SELECT；device scope B 不会被 scope A 的已持久 Change 跳过。

## 4. 数据迁移

新增 `0027_patrol_integrity_recovery`：

- PatrolRun logical/attempt/heartbeat/lease/progress 字段。
- NetworkChange dimensions。
- NetworkEvent correlation dimensions、first/last/latest/count 字段。
- `netops_network_event_occurrences` append-only 表及唯一约束、查询索引。

迁移只回填可确定的历史字段，不伪造历史 occurrence；历史 Event 的 opening 字段从现有
`run_id/source_change_id/occurred_at` 回填。

## 5. 发布门禁

- PostgreSQL normalization 批提交故障注入。
- 两个 detector 并发构造同一 tombstone。
- 双 Publisher + lease 过期/续约竞争。
- Worker kill、stale Run reconcile、Celery retry attempt 隔离。
- Redis 启动晚于 Worker、运行中断连和恢复。
- 真实设备第 N 条命令完成后 kill Worker。
- Event recurrence 在 opening Run 和 occurrence Run 的 Evidence Timeline 回放。

单元测试证明确定性语义；真实 PostgreSQL、Redis、Celery、设备连接器故障注入仍是上线门禁。

## 6. 实施结果（2026-07-11）

本计划的代码与数据模型改造已完成，未新增硬编码业务规则。EventRule 仍由现有
LLM 资产生成、验证、回放、审批链路进入 active runtime；手工能力仅保留为要求
`actor + reason` 且写入审计日志的 break-glass 接口。

| 阶段 | 状态 | 已落地能力 |
|---|---|---|
| Phase A | 已完成 | normalization 隔离失败 Collection、统一 fact time、确定性 tombstone、设备双身份兼容、非法时间拒绝 |
| Phase B | 已完成 | Change dimensions、无歧义 correlation、Event opening provenance、append-only EventOccurrence、跨 Run timeline |
| Phase C | 已完成 | 增量原子 manifest、命令级持久化回调、Run attempt/lease/reconcile、真实 Celery retry、有界 worker queue、Redis/Outbox lease 恢复 |
| Phase D | 已完成 | EventRule 审批门禁与审计强制入口、规则单次排序、Change 批写、Decision 定时清理、detector scope 修正 |
| Migration 0027 | 已应用 | 当前数据库 revision 与 migration head 均为 `0027_patrol_integrity_recovery` |

自动验证结果：

- `python -m compileall -q src`：通过。
- 本次修改文件 Ruff 检查：通过；仓库大型历史文件的既有行长/旧式 Optional 告警未在本次扩大修改。
- `pytest tests/unit tests/auth -q`：`501 passed, 1 failed`。
- 唯一失败为既有 `test_delete_plugin_removes_directory`：workflow 插件保存后未进入删除所用注册表；与巡检、事件、规则资产改动无关。
- `alembic current` 与 `alembic heads` 均为 0027；`alembic check` 仍报告本次改造前已经存在的索引、唯一约束和 server default 漂移，本次新增表和字段无缺失。

发布判定：代码与自动化回归达到合入条件；生产发布前仍必须执行第 5 节的外部故障注入，
特别是 Worker 强杀、Redis 运行中断连、双 Outbox Publisher 竞争和真实设备第 N 条命令后强杀。
