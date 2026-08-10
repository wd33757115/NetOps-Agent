# 巡检链路生产就绪审计与修复记录（2026-07-11）

<!-- document-status: superseded -->
> **历史文档，已被取代。** 本文仅保留审计背景，不再作为当前实现、操作或完成状态依据。
> 当前权威方案见[检查项中心的巡检与规则资产平台总体改造方案](check-item-centered-patrol-platform-remediation-2026-07-27.md)。

## 1. 目标与结论

本轮目标不是增加硬编码业务规则，而是在规则资产完善的前提下，使巡检链路在并发、重投递、进程崩溃、规则切换和历史回放场景中仍保持事实唯一、所有权明确、结果可重现和副作用幂等。

结论：静态审计确认的问题均已按阶段完成代码修复。空库迁移、真实 PostgreSQL/Redis/RabbitMQ 故障注入、516 项单元测试和前端生产构建均已通过。当前代码可作为生产发布候选；发布前仍须使用目标网络的脱敏 baseline/current 数据执行真实设备 golden patrol 验收，这属于环境与数据验收，不应由单元测试替代。

规则来源保持原设计：运行时读取已审批并激活的 LLM 生成 ObservationContract、ChangePolicy 和 EventRule；没有新增接口状态或光功率硬编码规则。手工内置规则接口仍由显式配置控制，仅作为兼容回退，并在分析开始时物化进不可变规则快照。

## 2. 逐项核查

| # | 审计问题 | 原判定 | 修复后状态 | 处理与证据 |
|---|---|---|---|---|
| 1 | recurrence 被 Consumer 按聚合 Event 去重 | 存在，P1 | 已关闭 | 幂等键使用 occurrence/message identity；真实 Redis 并发 claim 测试通过。 |
| 2 | redelivery attempt 非原子预留 | 存在，P1 | 已关闭 | 同一事务内加锁、fence 旧 attempt 并预留新 attempt；数据库唯一约束兜底。 |
| 3 | ACK 丢失后重复执行已完成巡检 | 存在，P1 | 已关闭 | terminal collection 是完成事实；redelivery 返回 `already_completed`，不再重跑 SSH。 |
| 4 | 0028 可能漏补 opening occurrence | 存在，迁移缺口 | 已关闭 | 后续迁移按 opening identity 修复并执行完整性 preflight。 |
| 5 | opening/recurrence occurrence kind 不一致 | 存在，P1 | 已关闭 | 运行时显式写入 opening、recurrence、recovery。 |
| 6 | recovery 未归入原故障时间线 | 存在，P1 | 已关闭 | recovery occurrence 追加到原聚合；投影更新受事件时间水位保护。 |
| 7 | 零 Snapshot direct run 在证据链不可见 | 存在，P2 | 已关闭 | Timeline 从 PatrolRun/CollectionResult 建立 run 事实，不再依赖 Snapshot 推导。 |
| 8 | Timeline 非 occurrence-native | 存在，P2 | 已关闭 | occurrence 保存 run、change、rule provenance 和投影判定元数据。 |
| 9 | 用户可见字符串存在错误解码残留 | 存在，P2 | 已关闭本轮命中 | `devices_api.py` 中已知错误分隔字符已替换；扫描测试通过。 |
| 10 | ChangeDecision 大批量单次写入 | 存在，P2 | 已关闭 | 固定大小分块持久化，避免参数和 SQL 体积无界增长。 |
| 11 | 并发 Change 可创建重复 active Event | 存在，P1 | 已关闭 | correlation advisory lock、行锁和 active unique index 三层保护。 |
| 12 | recurrence lost update、时间/provenance 倒退 | 存在，P1 | 已关闭 | 锁内递增；只有不早于水位的事实才更新聚合投影。 |
| 13 | Redis Stream pending 无 reclaim | 存在，P1 | 已关闭 | Consumer 使用 XAUTOCLAIM；真实 Redis PEL 测试通过。 |
| 14 | DLQ 写失败仍 ACK 原消息 | 存在，P1 | 已关闭 | DLQ 成功才 ACK；失败保留 pending。 |
| 15 | detecting/building_events/normalizing 可永久卡死 | 存在，P1 | 已关闭 | 三阶段统一 AnalysisAttempt lease、heartbeat、reconciler。 |
| 16 | retry 未绑定 immutable rule bundle | 存在，P1 | 已关闭 | attempt 冻结 ChangePolicy/EventRule 完整快照和 digest；retry 复用原快照。 |
| 17 | 存在 Change 被误当作 full-run 完成 | 存在，P1 | 已关闭 | 使用 scope completion；零 Change 同样可完成；移除结果存在性 shortcut。 |
| 18 | collection item identity 未贯穿事实链 | 存在，P2 | 已关闭 | execution_item_id 进入 fingerprint、Snapshot、CollectionResult 和 Observation provenance。 |
| 19 | Outbox occurrence 归属未由数据库强制 | 存在，P2 | 已关闭 | 历史回填后由迁移收紧 occurrence 唯一性和归属约束。 |
| 20 | Redis 7 天 fail-open 不适合不可逆副作用 | 存在，P1/P2 | 已关闭内建副作用 | claim 改为原子 lease；Audit/Notification 增加 durable idempotency key 和唯一约束。外部 ITSM 仍必须接收同一业务幂等键。 |
| 21 | AnalysisAttempt 无 generation/owner fencing | 存在，P0/P1 | 已关闭 | owner_token + attempt_number 条件更新；旧 worker 的 complete/fail 被拒绝。 |
| 22 | 分析输入变化但 scope 不变会复用旧结果 | 存在，P1 | 已关闭 | scope 加入输入 observation/snapshot/change revision digest。 |
| 23 | 迟到 recovery/fault 回退或重开错误投影 | 存在，P1 | 已关闭 | 全局锁顺序和事件时间水位；迟到事实只追加 occurrence，不回退聚合。 |
| 24 | active ChangePolicy 重复、EventRule 快照截断 | 存在，P1/P2 | 已关闭 | observation_type 全局唯一 active policy；EventRule 快照取消 2000 条静默/硬截断。 |
| 25 | replay 与原分析缺少显式派生身份 | 存在，P2 | 已关闭 | Change 保存 analysis_revision 与 rule_bundle_digest，幂等身份包含二者。 |
| 26 | 局部分析 scope 可覆盖整批 run 状态 | 存在，P1 | 已关闭 | 只有 full_run attempt 推进 run 级状态；局部 scope 仅维护自身 attempt。 |
| 27 | CollectionResult 重复命令在 detector 再次折叠 | 存在，P1 | 已关闭 | 按 execution item 聚合；删除判定要求同组采样全部 authoritative。 |
| 28 | Workflow 插件刚写盘后可能无法删除 | 回归发现 | 已关闭 | 删除前强制刷新注册表，仍通过受控 plugin_dir 删除。 |

## 3. 分阶段落地结果

### Phase A：事件聚合与时间线

- correlation 级事务锁、active 唯一约束、确定性全局锁顺序已落地。
- opening、recurrence、recovery 均形成 append-only occurrence 和独立 publication。
- 迟到事件保留为证据，但不能回退当前聚合投影。

### Phase B：Consumer 至少一次恢复与副作用幂等

- Redis claim 从 EXISTS/SET 竞争窗口改为 Lua 原子 claim/complete/release。
- pending reclaim、DLQ ACK 条件和 processing lease 已落地。
- Audit、Notification 使用 PostgreSQL durable idempotency receipt；不宣称跨任意外部系统的 exactly-once。

### Phase C：采集 attempt 所有权

- redelivery 决策区分 completed、in-progress 和新 attempt。
- broker 重投递会 fence 失联 owner；完成事实存在时不会重复 SSH。
- `logical_run_id + attempt_number` 唯一，runner 只能接管已预留 attempt。

### Phase D：可重放分析与事实身份

- normalizer、detector、event builder 全部接入 AnalysisAttempt fencing 和 heartbeat。
- scope 包含输入 revision，规则 bundle 在 attempt 开始时冻结。
- execution item、analysis revision、rule bundle digest 进入事实和派生身份。
- full-run 与局部 scope 的 run 状态所有权分离。

### Phase E：迁移、测试与发布门禁

- 新增 `0030_patrol_ownership_fencing` 与 `0031_patrol_fact_ownership`，包含数据 preflight、时区语义、Collection owner fencing、事实身份和 Parser Outbox claim。
- 从空数据库执行 `0001 -> 0031` 成功，`alembic check` 无 metadata drift。
- 真实 PostgreSQL/Redis/RabbitMQ 故障注入 10 项通过。
- 全量单元测试 519 项通过；前端 Vite production build 通过。

## 4. 发布门禁与剩余工作

代码级发布门禁已通过。以下是部署环境门禁，不属于继续修改核心代码的理由：

1. 使用生产厂商与型号的脱敏 baseline/current 数据运行完整导入、normalize、change、event、report 链路。
2. 验证接口 up/down 与光功率样本的 parser ready、事件命中、恢复和 no_event 原因。
3. 在 staging 执行一次 worker hard-kill、Redis consumer reclaim、RabbitMQ unacked redelivery 演练。
4. 核对部署前 ChangePolicy active 唯一性和迁移 preflight 输出。
5. 外部 ITSM、短信、邮件适配器必须透传 occurrence idempotency key，并由目标系统建立唯一约束。

仓库中的 `phase1_6_e2e_validation.py` 当前只接受 previous/current source 等参数，并不支持旧计划中的 `--profile`、`--quality-gate`。因此本轮没有伪造该命令的成功结果；应在提供真实脱敏数据目录后按脚本实际参数执行，或另立任务为脚本增加 profile 质量门禁接口。

## 5. 验证记录

```text
python -m compileall ...                                      PASS
ruff check（本轮关键模块，排除既有格式债务）                    PASS
pytest tests/unit -q                                          519 passed
pytest tests/integration/test_patrol_runtime_fault_injection.py -q
                                                             10 passed
空 PostgreSQL 数据库 alembic upgrade head                     PASS (0031)
空库与开发库 alembic check                                    PASS
0030 seeded data -> 0031 backfill                              PASS
npm --prefix web/react_frontend run build                     PASS
```

pytest 的无效 `env` 配置已移除，测试不再产生插件告警。Vite 仍提示主 bundle 偏大和 api.ts 同时被动态/静态导入，这是前端性能债务，不影响本轮巡检事实正确性。

历史 metadata drift 已通过模型与既有迁移语义对齐解决；当前开发库和临时空库均返回 `No new upgrade operations detected`。

## 6. 第二轮深审问题收口

| 问题 | 核查 | 最终处理 |
|---|---|---|
| 连续 Worker hard-kill / reserved hot requeue | 存在，P1 | 每次 broker redelivery 都在事务锁下 fence 旧 attempt 并分配新 generation；不再等待 900 秒旧 lease。 |
| Collection stale writer 写 Snapshot、Result 或复活 Run | 存在，P1 | PatrolRun 增加 owner_generation/owner_token；Snapshot、CollectionResult、touch、finish、completion marker 全部验证 owner。 |
| Change input revision 未包含 prior observations | 存在，P1 | scope digest 同时覆盖 current 与实际读取的 prior observations；历史 backfill 会创建新 attempt。 |
| normalizer 成功后仍停留 normalizing | 存在，P1/P2 | full-run 成功明确推进到 `normalized`，partial/failed 保持独立状态。 |
| Audit/Notify 逻辑事件 ID 随机 | 存在，P1/P2 | Skill/Workflow publisher 按逻辑 payload 生成稳定 UUID5；Parser 使用 outbox row ID。 |
| Consumer processing lease 无续约 | 存在，P1/P2 | handler 执行期间后台 heartbeat 续约；真实 Redis lease renewal 测试通过。 |
| Analysis heartbeat 由条数驱动 | 存在，P2 | 独立后台 heartbeat guard 按墙钟续约，不依赖 item 数量或单项处理时长。 |
| legacy v1 折叠不同 execution item | 存在，P2 | 有 execution_item_id 时禁止回退到 v1 fingerprint。 |
| terminal collection 早于 archive | 存在，P2 | archive 成功后才提交 terminal collection；完成事实代表后处理已完成。 |
| Event 同时间戳无总序 | 存在，P2 | 使用 fact time + event class rank + source change/event ID 总序。 |
| execution item 在 baseline join 丢失 | 存在，P1 | StateObservation 增加正式 execution_item_id，baseline SQL 精确 join execution item。 |
| Change crash retry 返回幽灵 ID | 存在，P1 | 新 Change 使用 deterministic UUID5；写入后按 idempotency key读取数据库 canonical ID，兼容旧随机主键。 |
| 多 EventRule 候选被错误折叠 | 存在，P1 | candidate ID 和幂等键包含 correlation、rule ID/version。 |
| Event batch 反序锁死锁 | 存在，P1/P2 | 事务开始先收集整个 batch 所需 locks，全局排序后一次获取；真实 PostgreSQL 并发测试通过。 |
| Recovery 发布两条消息 | 存在，P1/P2 | 采用 lifecycle occurrence 模型；命中原 fault 时只发布原 aggregate 的 recovery occurrence。 |
| Parser Outbox 双 publisher 与随机事件 ID | 存在，P1/P2 | pending -> publishing claim、token、lease、SKIP LOCKED；DomainEvent ID 固定为 outbox ID。 |
| Parser stale failure_code 判断错误 | 存在，P2 | 在修改状态前保存 previous_status，正确区分 dispatch_timeout/worker_timeout。 |
| CurrentState equal-time 不确定 | 存在，P2/P1 | Observation/CurrentState 保存 fact_order_key；数据库 upsert 使用 observed_at + fact_order_key。 |
| NetworkChange / no-event 巨型 SQL | 存在，P2 | Change 500 条、状态更新 1000 条分块。 |
| Consumer transient failure 直接 DLQ | 存在，P2 | 普通异常保留 pending 并计数重试；Permanent/Poison 或超过上限才 DLQ。 |
| Parser 状态机 lost transition | 存在，P2 | transition、dispatch、stale reconciliation 使用行锁；支持 expected_status CAS。 |
| 巡检事实 timestamp 混用无时区 | 存在，P2 | Snapshot、Result、Evidence、Observation、CurrentState、Change/Decision 统一 timestamptz，并迁移历史值。 |

第二轮验证额外覆盖：连续两次 takeover、旧 owner 的 Snapshot/Result/finish fencing、prior revision digest、cross-item baseline authority、canonical Change ID、同 event type 不同 correlation、反序 batch locks、Parser Outbox 单 owner、Redis processing lease renewal 和非空历史数据 backfill。
