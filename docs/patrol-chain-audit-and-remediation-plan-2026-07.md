# 巡检链路问题审计与分阶段修复计划

更新日期：2026-07-10

## 1. 目标与边界

本文件只讨论巡检事实链路，不把“规则资产是否足够丰富”作为前提问题。目标是在 ParserTemplate、ObservationContract、ChangePolicy、EventRule 都正确的情况下，保证以下链路不会因为运行状态、采集不完整、身份漂移或发布失败而静默漏报、误报或失去证据：

```text
PatrolRun
-> CollectionResult
-> CommandSnapshot
-> StateObservation / Tombstone
-> Baseline Selection
-> ChangeDecision / NetworkChange
-> EventDecision / NetworkEvent
-> NetworkEventOutbox
```

核心原则：未观测到不等于已删除；采集成功不等于集合完整；事实时间不等于处理时间；只有完成分析的可信 Run 才能成为后续基线。

## 2. 逐项审计结论

| # | GPT 提出的问题 | 审计结论 | 修复前证据 | 当前处理 |
|---|---|---|---|---|
| 1 | Failed Run 会成为下一轮 baseline | 存在，P0 | `StateRepository.list_prior_observations_for_run()` 仅按 mode 和 started_at 选历史 | Run 新增 `analysis_status`、`baseline_eligible`；只选择分析完成且在当前 Run 开始前已完成的基线 |
| 2 | 缺少 Collection completeness | 存在，P0 | 只有 Snapshot parse_status，没有 device + command + run 的完整性事实 | 新增 `PatrolCollectionResult`，记录 complete/empty/failed/ignored、删除权威性和基线资格 |
| 3 | 空 Collection 无法检测全量删除 | 存在，P0 | 删除检测依赖本轮至少有同 collection 的 Observation | 删除检测改为读取 CollectionResult；合法空集合也可进入 presence 判定 |
| 4 | 缺少 Tombstone，导致重复删除和无法识别重新出现 | 存在，P0 | Observation 只有正常 state；CurrentState 只 UPSERT | 新增 tombstone Observation；采用 present -> missing_candidate -> absent -> reappeared 状态机 |
| 5 | observation_type 没有贯通 ChangePolicy | 存在，P0 | ChangeDetector 从 metadata 猜，Normalizer 未写入 Contract 的 state_semantics | Observation 和 CurrentState 新增一等字段 `observation_type/semantic_type/unit`；Normalizer 从 Contract 写入 |
| 6 | 一台设备失败导致整批成功结果不持久化 | 存在，P0 | Runner 在 `result.success == false` 时先抛异常，Snapshot persister 不执行 | 只要至少一台成功就持久化成功设备，批次状态记为 partial；全失败仍失败 |
| 7 | CurrentState 有幽灵状态 | 存在，P1 | 缺失实体不会更新 CurrentState | 新增 active/stale/deleted 生命周期、missing_since、missing_count；tombstone 同步更新 CurrentState |
| 8 | 一个 Run 被每个设备导入过程反复 finish | 存在，P1 | `import_raw_capture()` 每次末尾调用 `finish_run()` | 在线巡检由外层 coordinator 创建一次、逐设备导入、最终 finalize 一次；离线单文件保留兼容入口 |
| 9 | EventRule runtime fail-open，builtin 会复活 | 存在，P1 | DB 空或异常时自动返回 builtin，并把缺失 builtin 合并回来 | Runtime 默认 DB authoritative、fail-closed；builtin 仅通过显式 bootstrap 或测试开关使用 |
| 10 | Event 持久化与 EventBus 发布没有事务边界 | 存在，P1 | 先保存 Event，再发布当前内存对象，重试可能出现不同 event_id | 新增 `NetworkEventOutboxRecord`；Event 与 Outbox 同事务写入，publisher 只发布 canonical event_id |
| 11 | ChangePolicy provenance 丢失 | 存在，P1 | Policy compare 返回版本信息，但 NetworkChange 未保存 | NetworkChange 保存 observation_type、policy/version、compare mode、decision reason、policy snapshot |
| 12 | Contract breaking change 缺少 rebaseline 门禁 | 存在，P1 | Observation 路径没有 entity_type/primary_keys 兼容性检查 | Contract 激活比较 state identity；breaking 变更必须带已批准的 rebaseline 声明 |
| 13 | inventory_device_id 未稳定贯通 | 部分存在，P1 | Snapshot 通过 name + IP 反查，失败后退回外部 ID | 在线巡检直接传递 Inventory UUID；离线导入仍允许 external fallback，并在 metadata 标记身份状态 |
| 14 | observed/detected/processed 时间混用 | 存在，P1 | Event occurred_at 默认使用 ChangeDetector 执行时间 | Observation 路径区分 observed_at、detected_at、processed_at；Event occurred_at 优先使用 observed_at |
| 15 | 大规模巡检没有并发控制 | 存在，P1 | 对全部设备直接 `asyncio.gather()` | 增加可配置全局 Semaphore，默认 50；站点/凭据域配额列入容量阶段 |
| 16 | save_baseline 没有控制后续 Change/Event | 存在，P1 | 参数只返回 `baseline_saved`，Workflow 后续照常执行 | 新增 run_purpose；baseline Run 正常归一化和更新 CurrentState，但 ChangeDetector 返回零变化 |
| 17 | Recovery 关系硬编码在 Python | 存在，P1 | repository 内维护 event_type 映射表 | EventRule 资产增加 event_class、recovery.resolves、correlation keys；生成器和 builtin 显式携带，repository 只消费资产 |
| 18 | EventRule 只能 0..1，优先级由名称决定 | 存在，P1/P2 | `for rule: return first`，数据库按 name 排序 | 支持 0..N；按 priority 降序；exclusive 命中后停止；规则名只用于稳定次序 |

结论：原分析中的 18 项全部有代码依据；第 13 项属于在线路径和离线路径风险不同，其余均为真实缺陷。基础 happy path 能运行，但修复前不满足生产可信链路要求。

## 3. 分阶段落地计划

### Phase 1：可信 Run 与 CollectionResult（已实施）

目标：先消除静默漏报和整批数据丢失。

- 建立 Run 的 collection_status、analysis_status、baseline_eligible、run_purpose。
- 建立命令级 CollectionResult，完整性结论必须由 collector 与 parser 联合给出。
- 在线批次由 coordinator 原子 finalize；部分成功保留成功设备数据。
- 基线选择只接受可信、已完成且时间顺序确定的 Run。
- 在线链路贯通 inventory_device_id；限制全局采集并发。

验收门禁：失败 Run 不参与基线；99/100 成功时 99 台进入 Snapshot；同一 Run 只 finalize 一次。

### Phase 2：Presence/Tombstone 与安全删除（已实施）

目标：正确表达“未观测、疑似缺失、确认不存在、重新出现”。

- 只有 authoritative_for_deletion=true 的 CollectionResult 可以触发缺失判断。
- 第一次完整采集缺失写 missing_candidate/tombstone，CurrentState 变 stale。
- 连续两次完整采集缺失后确认 absent/deleted，CurrentState 变 deleted。
- 实体重新出现生成 reappeared，避免永远残留幽灵状态。
- 空但合法的完整 Collection 可以参与全量缺失检测；失败、截断、未知集合禁止推导删除。

验收门禁：partial/truncated 不产生删除；连续缺失不重复产生 deleted；重新出现可检测。

### Phase 3：语义、时间、身份和策略证据（已实施，容量验收待完成）

目标：让每个 Change 可以解释“使用了什么事实、语义和规则版本”。

- Observation 显式保存 observation_type、semantic_type、unit。
- Change 保存 Policy provenance 和 observed/detected/processed 三类时间。
- Event occurred_at 继承事实 observed_at。
- Contract state identity breaking change 必须 rebaseline approval。
- 在线巡检使用 Inventory UUID；外部 ID 仅作为离线兼容回退。
- 全局并发上限已实现；per-site/per-credential-domain 配额在容量测试后配置。

验收门禁：接口 protocol 能命中 interface.oper_status Policy；Policy 升级后历史 Change 仍可解释；并发 Run 不倒置 baseline。

### Phase 4：Event runtime 治理和可靠发布（已实施）

目标：规则资产是真正的运行时权威，事件发布可重试且引用稳定。

- Production runtime 不合并 builtin，不在 DB 故障时降级生成未经批准的事件。
- 保留 `ensure_default_rules()` 和 bootstrap API，作为显式手工内置规则接口。
- EventRule 支持 priority、exclusive、0..N、event_class、recovery、correlation keys。
- Recovery resolver 不再维护 Python event type 映射。
- Event 和 Outbox 同事务持久化，Celery 定时发布 pending outbox。

验收门禁：deprecated builtin 不复活；DB 不可用时不生成事件；EventBus 中 event_id 必须能在数据库定位。

### Phase 5：生产容量与可视化验收（后续发布门禁）

目标：验证上述语义在生产规模和 UI 证据链上可运营。

- 增加 per-site、per-credential-domain、per-vendor 并发配额和压测参数。
- Patrol Batch Report 展示 collection completeness、baseline eligibility、presence transition、policy/rule provenance、outbox lag。
- Evidence Timeline 展示 observed/detected/processed 时间和 no_event reason。
- 对 Contract breaking change 提供 rebaseline 审批界面和影响预估。
- 执行 PostgreSQL 故障、Redis 故障、Workflow 中断、重复投递、并发 Run 的故障注入测试。

这一阶段不改变事实语义，只完成容量参数、运维可见性和发布演练。

## 4. 数据兼容与回滚

- 迁移：`0024_patrol_chain_trust_model`、`0025_patrol_schema_alignment`。
- 旧 completed Run 回填为 collection completed、analysis completed、baseline eligible；新 Run 必须走完整生命周期。
- 旧 EventRule recovery 类型在迁移中回填 event_class/recovery.resolves。
- 新 runtime 默认不启用 builtin fallback；需要保留手工规则时应先执行显式 bootstrap，使其成为数据库中的受治理资产。
- 回滚数据库前必须先停止新版本 worker，避免旧代码无法识别 tombstone、Outbox 和 0..N 规则语义。

## 5. 测试策略

单元与组件门禁：

```powershell
venv\Scripts\python.exe -m pytest tests\unit\test_patrol_trust_model.py tests\unit\test_device_patrol_runner.py tests\unit\test_state_normalizer.py tests\unit\test_event_rules.py tests\unit\test_event_rule_generator_skill.py tests\unit\test_patrol_change_event_pipeline.py tests\unit\test_observation_contract.py tests\unit\test_runtime_rule_assets.py -q
```

迁移门禁：

```powershell
venv\Scripts\python.exe -m alembic upgrade head
venv\Scripts\python.exe -m alembic current
```

发布前还必须执行真实 PostgreSQL/Redis/Celery 的 golden patrol E2E，并验证：部分批次、空集合、连续缺失、恢复、规则库不可用、Outbox 重试、并发 Run 和 baseline 模式。
