# 巡检运行时并发与恢复修复计划（2026-07-11）

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

## 1. 目标与边界

本轮不扩展 Parser 或手工内置业务规则，目标是让已有规则完善时，巡检运行时在并发、重投、崩溃和回放条件下仍保持事实唯一、结果可恢复、语义可重现。

核心不变量：

1. 同一 `event_type + correlation_key` 最多只有一个 active Event。
2. opening、recurrence、recovery 都是独立且不可变的 EventOccurrence；每个 occurrence 只对应一个 Outbox publication。
3. Consumer 按 occurrence 幂等，并保证未完成消息最终可再次领取；DLQ 未持久化时不得 ACK 原消息。
4. 同一 logical patrol run 的 attempt 必须先在数据库原子预留；成功完成后的 ACK 丢失不得触发重复 SSH 巡检。
5. 分析完成由显式的 scope completion 记录证明，不能由“已经存在 Change”推断。
6. 同一分析 attempt 固定使用同一份 ChangePolicy/EventRule bundle；显式 replay 才允许使用新资产。
7. `collection_item_id` 或确定性 ordinal 必须贯穿计划、采集、Snapshot、Result 和 Observation provenance。

## 2. 最新审计逐项判定

| # | 问题 | 判定 | 当前证据与影响 |
|---|---|---|---|
| 1 | recurrence 被 Consumer 按 Event ID 去重 | **存在，P1** | Outbox 有 `occurrence_id`，但 `StreamConsumer` 使用 `event.event_id` 作为幂等键；同一 Event 的后续 occurrence 会被 ACK 后跳过。 |
| 2 | redelivery attempt 不是原子预留 | **存在，P1** | repository 在事务中只计算并返回 next attempt，没有插入占位 Run；并发 worker 可得到相同编号。 |
| 3 | ACK 丢失会重跑已完成巡检 | **存在，P1** | redelivered 一律分配新 attempt，没有 `already_completed` 决策。 |
| 4 | 0028 可能漏补 opening occurrence | **存在，P1 数据迁移缺口** | SQL 检查“没有任何 occurrence”，而不是“没有 opening occurrence”。 |
| 5 | occurrence kind 不一致 | **存在，P1** | 历史回填使用 `opening`，运行时 opening/recurrence 都写成 `observed`。 |
| 6 | recovery 未追加到原故障 Event | **存在，P1** | recovery 会更新原 Event 状态，但 occurrence 只挂到新 recovery Event，原聚合时间线不完整。 |
| 7 | 零 Snapshot 的 direct patrol 在 Timeline 不可见 | **存在，P2** | direct run ID 仅从 CommandSnapshot 推导。 |
| 8 | Timeline 不是 occurrence-native | **存在，P2** | 当前主要展示 Event 聚合态，不能稳定呈现每次 occurrence 的 run/change/provenance。 |
| 9 | 源码仍有乱码且扫描范围窄 | **存在，P2** | `devices_api.py` 等仍有 mojibake；现有 token 扫描不能覆盖常见错误解码模式。 |
| 10 | ChangeDecision 大批量插入不分块 | **存在，P2** | 单个 `INSERT ... VALUES` 会随大型巡检批次增长，存在参数数和 SQL 体积风险。 |
| 11 | 并发 Change 可创建重复 active Event | **存在，P1** | “先查后插”无 correlation 锁，数据库也没有 active partial unique index。 |
| 12 | 并发 recurrence 丢计数且 provenance 可倒退 | **存在，P1** | recurrence_count 为 read-modify-write；last_seen/latest 字段无行锁且无 observed_at 单调保护。 |
| 13 | Redis Stream 没有 PEL reclaim | **存在，P1** | Consumer 只用 `XREADGROUP ... >`，worker 在 handle 前后崩溃会使消息永久 pending。 |
| 14 | DLQ 发布失败仍 ACK 原消息 | **存在，P1** | DLQ 返回值未参与 ACK 决策，异常消息可能永久丢失。 |
| 15 | analysis_status 可卡在 detecting/building_events | **存在，P1** | 阶段缺少独立 lease/heartbeat/completion，现有 stale reconciler 主要覆盖 collection running。Observation 保留使事实可恢复，但解释链不会自动收敛。 |
| 16 | ChangePolicy/EventRule 未绑定 immutable bundle | **存在，P1** | retry 会重新读取 active 资产，同一 Evidence 可能因中途激活新版本而产生不同结论。 |
| 17 | “存在 Change”被误当作 full-run 完成 | **存在，P1** | partial device scope 先写入后，full-run shortcut 会跳过其他设备；零 Change 也没有完成标记。 |
| 18 | `collection_item_id` 在事实链中丢失 | **存在，P2** | manifest 未保留 item identity，CollectionResult 唯一键按 command 折叠重复采样。 |
| 19 | Outbox `occurrence_id` 仍 nullable | **存在，P2** | 新设计不变量只由应用约定，数据库允许产生无法追溯的 publication。 |
| 20 | Redis 幂等 fail-open 且 TTL 仅 7 天 | **存在，按 Consumer 可升 P1** | Redis 故障和长期 replay 会重复执行。平台层应 fail-closed；外部不可逆副作用还必须在目标系统按 occurrence 使用 durable idempotency key。 |

静态代码足以确认上述缺口。并发重复 Event、lost update、PEL crash recovery 仍必须通过 PostgreSQL/Redis 故障注入测试证明修复有效，而不是仅以单元测试替代。

## 3. 分阶段落地

### Phase A：Event 聚合与 occurrence 生命周期

实施：

- 对 correlation 获取事务级 advisory lock，并对命中的 active Event 使用行锁。
- 增加 active Event partial unique index，作为应用锁之外的最终数据库防线。
- recurrence 在锁内递增；只有更新后的 observation 时间不早于当前 `last_seen_at` 时，才更新 latest provenance。
- `_record_event_occurrence` 必须显式接收 `opening / recurrence / recovery`。
- recovery occurrence 追加到原故障 Event 并发布对应 Outbox；兼容保留已有 recovery Event 表达。
- 修补历史 opening；确认所有 Outbox 均有 occurrence 后将列改为 `NOT NULL`。

验收：

- 两个并发事务命中同一 correlation 后只有一个 active Event。
- N 个并发 recurrence 最终 `recurrence_count` 精确增加 N，`last_seen_at` 不倒退。
- 每个 Event 至少有 opening；原故障 Event 可看到 recovery occurrence。

### Phase B：Consumer 至少一次恢复语义

实施：

- 幂等键改为 `payload.occurrence_id or event_id`。
- 周期执行 `XAUTOCLAIM` 领取超过 idle 阈值的 pending 消息，再读取新消息。
- DLQ 成功后才 ACK；DLQ 失败保留 pending。
- Redis 幂等读写失败时 fail-closed，不调用 handler、不 ACK。
- 外部副作用 Consumer 必须把 occurrence ID 传给目标系统并建立 durable unique receipt；Redis 只作为消费加速层，不宣称 exactly-once。

验收：

- 同一 Event 的 3 个 occurrence 均处理一次；单个 occurrence 重投不重复处理。
- 模拟 worker 在 ACK 前退出，另一 consumer 能从 PEL 领取并完成。
- 模拟 DLQ/幂等存储故障，原消息保持 pending。

### Phase C：Patrol redelivery 原子决策

实施：

- 增加 `UNIQUE(logical_run_id, attempt_number)`，并把已回填的 logical identity 设为非空。
- 在同一事务中锁 logical run、终结失效 running attempt、插入 reserved attempt。
- 返回结构化 `RedeliveryDecision`：`allocate_new_attempt / already_completed / reuse_running / reject_duplicate`。
- runner 只接管属于当前 worker 的 reserved attempt；owner 冲突必须拒绝。
- latest attempt 已完成时返回稳定结果，不再次执行 SSH。

验收：

- 两个并发 redelivery 不会获得相同 attempt。
- 完成后模拟 broker ACK 丢失，不产生新 Run、不产生新 Snapshot。

### Phase D：可重放分析与事实身份

实施：

- 新增 `PatrolAnalysisAttempt`，记录 `run_id + stage + scope_hash + rule_bundle_digest`、lease、状态和结果摘要。
- detector 删除“存在 Change 即完成”的 shortcut；相同 scope 的 completed marker 才可复用，包括零 Change。
- 分析开始时冻结 ChangePolicy/EventRule 的版本 ID、规范化内容和 digest；retry 从快照解析，不能读取新 active 版本。
- 阶段异常统一写 failed；reconciler 收敛 lease 过期的 detecting/building_events。
- 统一 `execution_item_id = collection_item_id or deterministic ordinal`，贯穿 manifest、Snapshot、CollectionResult 和 Observation metadata。
- ChangeDecision 批量写入按固定大小分块。
- Timeline 直接从 PatrolRun/CollectionResult 纳入零 Snapshot run，并以 occurrence 展示 run/change/rule provenance。

验收：

- partial device scope 不会让 full-run 被错误短路；零 Change retry 可稳定复用。
- 分析期间激活新规则不改变当前 attempt 结果；显式 replay 创建新 attempt。
- 同一命令的两次不同 collection item 不折叠。
- 超时分析会自动转为 failed/retryable，UI 不再永久显示处理中。

### Phase E：质量、迁移与发布

- migration 先做数据 preflight；发现重复 active Event 时阻止上线并输出 correlation 清单，不静默合并历史事实。
- PostgreSQL 集成测试覆盖 unique index、advisory lock、并发 recurrence、attempt allocation。
- Redis 集成测试覆盖 PEL reclaim、DLQ 故障与幂等故障。
- 加强 mojibake 检查：扫描 replacement character、UTF-8/GBK 常见错码序列和用户可见字符串；逐文件人工确认后修复。
- 发布顺序：数据库兼容迁移 -> 双读兼容代码 -> backfill/验证 -> `NOT NULL`/unique 约束 -> 删除旧路径。
- 回滚只回滚应用版本；已写入的 occurrence、attempt 和 analysis record 均保留，不删除事实。

## 4. 实施状态

- [x] 审计问题静态核查
- [x] 修复设计与阶段门禁
- [x] Phase A：Event 并发与 occurrence
- [x] Phase B：Consumer 恢复语义
- [x] Phase C：redelivery 原子预留
- [x] Phase D：分析快照、scope 与事实身份
- [ ] Phase E：单元测试和离线迁移验证已通过；PostgreSQL/Redis 故障注入待基础设施可用后执行

当前验证记录：

- `compileall`：通过。
- Ruff 本轮变更关键规则（`F/E9/I/N818`）：通过。
- Consumer、CLI item identity、redelivery/bulk insert、规则快照重建与乱码门禁：15 项测试通过。
- Alembic head：`0029_patrol_runtime_concurrency`；PostgreSQL model DDL 和 0029 offline SQL 编译通过。
- `devices_api.py` 扩展 mojibake 扫描：通过。
- 当前机器 Docker Desktop/PostgreSQL/Redis 未运行，因此 advisory lock 并发、真实 PEL reclaim 和迁移 upgrade 尚未标记通过。

## 5. 非目标

- 不硬编码接口/光衰等业务规则。
- 不绕过现有 LLM 规则生成、验证、审批和激活链路。
- 本轮保留手工内置规则扩展接口，但运行时一致性修复与规则来源无关。
- 不以 Redis 幂等标记替代外部 ITSM、短信、邮件或自动化系统自己的 durable idempotency。
