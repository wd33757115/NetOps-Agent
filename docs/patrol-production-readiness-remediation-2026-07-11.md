# 巡检链路生产就绪审计与修复记录（2026-07-11）

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

- 新增 `0030_patrol_ownership_fencing`，包含数据 preflight、时区语义修正、所有权与幂等约束。
- 从空数据库执行 `0001 -> 0030` 成功。
- 真实 PostgreSQL/Redis/RabbitMQ 故障注入 6 项通过。
- 全量单元测试 516 项通过；前端 Vite production build 通过。

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
pytest tests/unit -q                                          516 passed
pytest tests/integration/test_patrol_runtime_fault_injection.py -q
                                                             6 passed
空 PostgreSQL 数据库 alembic upgrade head                     PASS (0030)
npm --prefix web/react_frontend run build                     PASS
```

已知非阻断项：pytest 输出一条 `Unknown config option: env` 告警，说明当前环境未安装或未启用 pytest-env；Vite 提示主 bundle 偏大和 api.ts 同时被动态/静态导入。这两项是工具链与前端性能债务，不影响本轮巡检事实正确性，但应进入后续性能与构建治理清单。

`alembic check` 还报告了仓库历史遗留的跨模块 metadata drift，包括 AgentModuleControl、CollectionPolicy、CommandProfile、DynamicGroup、RawEvidence 以及部分 ChangePolicy/EventRule 的 index/unique 表达差异和若干 server default 差异。它不影响已经验证通过的空库 `upgrade head` 和本轮运行时约束，但会污染未来 autogenerate，建议独立建立 schema reconciliation migration；不应把这些跨模块变更混入本轮巡检 ownership 修复后直接自动应用。
