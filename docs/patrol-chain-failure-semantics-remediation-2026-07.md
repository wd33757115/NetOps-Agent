# 巡检链路第二轮审计与失败语义修复计划

更新日期：2026-07-10

## 1. 审计结论

本轮基于提交 `69bb3521fcd4f58059c5afcfc908ba938d796a6b`，重点检查并发 Run、命令中途失败、Workflow 重试、Outbox 重复投递和大批量写入。上一轮的 CollectionResult、Tombstone、Observation 语义、Rule 治理和基础 Outbox 已经成立，但以下 12 项仍真实存在。

| # | 问题 | 结论 | 修复前证据 | 优先级 |
|---|---|---|---|---|
| 1 | PatrolRun 在采集结束后创建，started_at 不是真实采集开始 | 存在 | Runner 先 `run_plan_or_filter()`，后调用 snapshot persister 创建 Run | P0 |
| 2 | Tombstone/CurrentState 与 NetworkChange 分事务提交 | 存在 | `record_absence()` 自行提交，Change 在循环结束后另存 | P0 |
| 3 | Outbox envelope 使用随机 DomainEvent ID 和发布时间 | 存在 | publisher 未传 `event_id/occurred_at`，Consumer 以 envelope ID 幂等 | P0 |
| 4 | 单设备多命令仍是 all-or-nothing | 存在 | Collector 完成全部命令后才写文件，任一异常丢失内存中的成功输出 | P1 |
| 5 | 整批设备完成后才持久化 | 存在 | `asyncio.gather()` 完成后 Runner 才导入所有文件 | P1 |
| 6 | CollectionResult.baseline_eligible 未参与 Observation 基线查询 | 存在 | StateRepository 只过滤 Run 级资格 | P1 |
| 7 | Outbox 没有 claim/lease/backoff/dead-letter | 存在 | 普通 SELECT pending；publish 返回 None 不记失败 | P1 |
| 8 | correlation_keys 没有成为 Event Repository 一等身份 | 存在 | payload 动态生成 dedupe key，Repository 仍按固定实体字段查询和恢复 | P1 |
| 9 | PATROL_MAX_CONCURRENCY 只是单批次限制 | 存在 | Semaphore 在每次 `patrol_by_filter()` 内创建，不能跨 Worker | P1 |
| 10 | 每个 Change 都重新加载和排序 EventRule | 存在 | `build_events` 循环内调用 `match_event_rules -> load_event_rules` | P2 |
| 11 | suppressed ChangePolicy decision 不持久化 | 存在 | Policy 返回 changed=false 后直接 continue | P2 |
| 12 | Observation 和 CurrentState 逐条写入 | 存在 | 每个 Observation 单独 INSERT，再单独 UPSERT CurrentState | P2 |

## 2. 工程原则

- 事实先于派生：PatrolRun 必须在采集前存在；每条成功命令完成后即可形成持久事实。
- 共同命运事务：Tombstone、CurrentState 生命周期和对应 Change 必须同事务提交或回滚。
- 端到端幂等：NetworkEvent.id、Outbox.event_id、DomainEvent.event_id 必须一致。
- 显式状态机：Outbox 使用 pending/publishing/retry_wait/published/dead_letter，不依赖定时器碰运气。
- 可信基线双门禁：Run 和 Collection 都必须 eligible，事实时间不得倒序。
- 分布式配额：进程内 Semaphore 只作为第二层保护，全局上限由 Redis lease semaphore 承担。
- 批量数据路径：Observation 写入按 Snapshot 批量 INSERT/UPSERT，避免 N+N 次数据库往返。

## 3. 分阶段修复计划

### Phase A：采集时序和增量持久化

- Runner 在设备采集前创建 PatrolRun，传入真实 execution_start。
- Collector 每条命令完成后立即追加并 flush 原始报告，同时写命令结果 manifest。
- 命令失败时保留此前成功命令，后续命令标记 not_executed。
- PatrolTool 通过 device-result callback 在设备完成时立即持久化，不等待全批结束。
- 批次结束只做汇总和 finalize，不重复导入。

验收：started_at 等于采集开始；第 10 条超时仍保存前 9 条；长批次中已完成设备在 Worker 中断前已进入 PostgreSQL。

### Phase B：Presence/Change 原子事务与 Decision 审计

- 将 absence observation 构造与持久化拆开。
- 同一事务批量写 Tombstone、更新 CurrentState、写 NetworkChange、写 ChangeDecision。
- detector 重试优先复用当前 Run 已持久化的 Change，避免语义从 missing/deleted 变为 modified。
- suppressed Policy decision 写短期审计表，记录 policy/version、reason 和 value 摘要。
- Observation/CurrentState 改为批量 INSERT/UPSERT。

验收：任一步骤失败时三类数据全部回滚；重试返回相同 change_type 和 change_id；suppressed 可解释。

### Phase C：Event/Outbox 分布式幂等

- NetworkEvent 增加一等 `correlation_key` 并建立活动事件查询索引。
- EventBuilder 分离 correlation key 与 event-type dedupe key。
- Recovery 直接按 target event_type + correlation_key 关闭事件。
- Outbox 使用 `FOR UPDATE SKIP LOCKED` 原子 claim，带 claim_token 和 lease_until。
- 失败进入指数退避；超过最大次数进入 dead_letter。
- EventBus envelope 复用 canonical NetworkEvent.id 和 occurred_at；publish 返回 None 视为失败。

验收：两个 publisher 不能同时 claim 同一行；重复发布的 envelope ID 不变；自定义 correlation keys 能去重和恢复。

### Phase D：分布式容量控制

- Redis sorted-set lease semaphore 提供跨 Task/Worker 的全局 SSH 配额。
- 保留单批次 Semaphore，Redis 不可用时进入显式 degraded fallback 并记录状态。
- EventRule 在每次 EventBuilder 调用中只加载和排序一次。
- 增加批量 Observation 写入计数和容量测试。

验收：多个 Patrol Task 的总租约不超过全局上限；1000 个 Change 只加载一次规则；单 Snapshot 的 Observation 写入为常数次数据库语句。

## 4. 数据迁移

新增迁移 `0026_patrol_failure_semantics`：

- `netops_network_events.correlation_key`
- Outbox claim/lease/retry/dead-letter 字段
- `netops_network_change_decisions` 短期审计表
- 必要索引和历史 Event correlation_key 回填

历史 Observation 没有 CollectionResult 可信证据时不自动获得 Collection 级 baseline 资格；需要重新执行 baseline 巡检，避免用未知完整性的历史集合做生产基线。

## 5. 故障注入门禁

- 两个并发 Run 交叉完成，禁止未来 Observation 成为过去 Observation 的 baseline。
- 第 N 条命令超时，前 N-1 条进入 Snapshot，剩余命令状态明确。
- Tombstone 写入后模拟 Change 插入失败，验证事务整体回滚。
- Outbox publish 成功但 mark 前中断，再次发布使用同一 DomainEvent ID。
- 两个 publisher 并发 claim，只允许一个获得同一 Outbox。
- Redis 不可用时 Outbox 进入 retry_wait；达到阈值进入 dead_letter。
- 两个 Celery Patrol Task 并发时，全局 SSH 租约不超过配置上限。

真实 PostgreSQL、Redis、Celery 和设备连接器故障注入是生产发布门禁；单元测试不能替代该验收。

## 6. 实施结果（2026-07-10）

| 阶段 | 代码实施 | 自动化证据 | 发布前仍需完成 |
|---|---|---|---|
| Phase A | 已完成 | command partial、PatrolTool、Runner 回归通过 | 真实设备第 N 条命令超时和 Worker 中断注入 |
| Phase B | 已完成 | tombstone/decision 同批持久化、批量 upsert、重试语义回归通过 | PostgreSQL 事务中点强制断连测试 |
| Phase C | 已完成 | canonical event identity、规则匹配、现有事件流水线回归通过 | 双 publisher 并发 claim、publish 后 mark 前进程终止 |
| Phase D | 已完成 | Redis lease acquire/renew、每批规则单次加载测试通过 | 多 Celery Worker 长时间压力测试与 Redis 故障演练 |

迁移已在本地 PostgreSQL 应用，`alembic current` 与 `alembic heads` 均为
`0026_patrol_failure_semantics`。本次相关 Ruff 检查通过。

全量单元测试结果为 479 项通过、1 项失败。失败项是既有
`test_workflow_governance.py::test_delete_plugin_removes_directory`，与巡检改造无关，
单独复跑仍失败。`alembic check` 仍报告多项 0026 之前已经存在的模型/迁移漂移；
输出中没有本次新增表或字段。上述两项应单独治理，不应混入巡检链路提交。
