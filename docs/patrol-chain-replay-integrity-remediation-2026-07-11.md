# 巡检链路回放一致性与崩溃恢复修复计划

更新日期：2026-07-11  
审计基线：`58a867030e1bb6d4d40150d16d3c6a3aaa4d145b`

## 1. 项目目标

在上一轮事实一致性改造基础上，消除增量持久化对 Snapshot/Observation/Change/Event 的重复污染，保证历史 Evidence 按采集时绑定的不可变 Contract 回放，并让历史事件、直接巡检和 Worker 强杀恢复都进入同一条可审计链路。

必须保持以下 invariant：

1. 同一 Run 内同一命令事实，无论被 progress callback 或 final callback 重放多少次，只能有一个 CommandSnapshot。
2. 采集链路不因 Parser/PostgreSQL 的瞬时故障停止后续 SSH 命令；失败必须持久记录并可重放。
3. `Evidence + parser_template_id + parser_version + contract_version` 决定唯一 Observation 语义；历史回放不得静默切换 active Contract。
4. 每个历史或新增 Event 至少有一个 opening EventOccurrence；每次 recurrence 都有独立 occurrence 和 outbox publication。
5. Worker hard kill 后，原 attempt 收敛为 abandoned，新投递使用独立 attempt，不混写事实。
6. Device Evidence Timeline 必须按设备事实查询，不能依赖全局最近 500 个 WorkflowRun，也不能排除 direct patrol。

## 2. 逐项核验

| # | 最新分析问题 | 结论 | 证据与处理 |
|---|---|---|---|
| 1 | progress callback 导致 O(n²) Snapshot | 存在，P0 | fingerprint 包含增长中的 whole-file hash；改为 command fact identity |
| 2 | progress persistence 异常中断采集 | 存在，P1 | callback 未隔离；记录失败并继续，final callback/任务重试补偿 |
| 3 | Snapshot replay 未锁定 Contract version | 存在，P1 | Normalizer 只读取 active；增加 exact-version resolver，缺失时 fail closed |
| 4 | 0027 未回填 EventOccurrence，历史 Timeline 消失 | 存在，P1 | INNER JOIN occurrence；0028 回填 deterministic opening occurrence，并保留查询兼容 |
| 5 | hard kill 只 abandoned，不自动续跑 | 能力缺口，P1 | patrol task 启用 late ack/worker-lost reject，redelivery 分配新 attempt |
| 6 | Timeline 全局最近 500 后 Python 过滤且排除 direct patrol | 存在，P1 | 改为设备 Snapshot/PatrolRun 驱动，Workflow 仅作可选关联 |
| 7 | recurrence 无新 Outbox | 存在，P2 | 增加 occurrence_id 维度，每个 occurrence 一条幂等 outbox |
| 8 | ChangeDecision 仍逐条 SELECT/INSERT | 存在，P2 | 改为批量 PostgreSQL upsert |
| 9 | 源码存在 mojibake | 存在，P2 | 集中在 devices_api 用户可见标签；恢复可读中文并加扫描测试 |

Outbox 在 Publisher lease 过期时仍是 at-least-once，不宣称 exactly-once；Consumer 必须按 canonical occurrence/event identity 幂等。

## 3. 分阶段落地方案

### Phase A：Snapshot 事实身份与采集解耦

- 在线 command fingerprint 使用 canonical JSON/SHA-256：`run_id + inventory_device_id + canonical_command + observed_at + raw_text_hash`。
- `source_file_hash` 只作为 provenance，不再参与在线 command identity。
- 保持离线无 Run 导入的 file-level fingerprint 兼容。
- progress callback 异常写入 manifest 的 `persistence_status/error`，不中断下一条命令。
- final device callback 重放完整 manifest；稳定 fingerprint 保证补偿无重复。

验收：3 条命令、3 次 progress callback、1 次 final callback后，恰好 3 Snapshot、3 Evidence；重复 normalization 不产生额外 Observation/Change。

### Phase B：版本化回放与历史事件迁移

- ObservationContractRepository 增加 exact-version 查询。
- Normalizer 在 Snapshot 有 `contract_version` 时只读取该版本；缺失返回 `contract_version_unavailable` 并 quarantine，不回退 active。
- 新增 0028 migration，为无 occurrence 的历史 Event 回填 deterministic opening occurrence。
- Timeline 在滚动升级期间兼容 `Event.run_id` opening provenance，迁移后以 occurrence 为主。

验收：active 从 v1 升到 v2 后重放 v1 Snapshot，仍产生 v1 语义；删除 v1 后重放 fail closed；旧 Event 升级后仍出现在 opening Run Timeline。

### Phase C：崩溃恢复与设备事实 Timeline

- patrol Celery task 启用 `acks_late` 与 `reject_on_worker_lost`。
- Broker redelivery 时原 running attempt 原子转 abandoned，并分配单调递增的新 attempt。
- Timeline 先按设备身份查询 PatrolRun/CommandSnapshot，direct patrol 与 workflow patrol 均可见。
- WorkflowRun 只补充 workflow/step/execution plan 元数据，不再决定事实是否可见。

验收：模拟 redelivery 创建 attempt-1，attempt-0 不再 running；设备拥有超过全局 500 Run 之外的历史仍可查询；direct patrol 出现在 Timeline。

### Phase D：Occurrence 发布、批量决策与文本质量

- Outbox 增加 `occurrence_id`，移除 `event_id` 单行唯一限制，改为 occurrence 唯一。
- opening 与 recurrence 都创建 occurrence 后在同一事务写 outbox。
- ChangeDecision 使用 `INSERT ... ON CONFLICT DO NOTHING RETURNING` 批量持久化。
- 修复 devices_api 中用户可见 mojibake，新增源文件扫描门禁。

验收：同一 active Event 三次命中产生 1 Event、3 Occurrence、3 Outbox；重复重放仍为 3；1000 条 Decision 无逐条 SELECT。

## 4. 数据迁移与回滚

新增 `0028_patrol_replay_integrity`：

- 回填历史 Event 的 deterministic opening occurrence。
- `netops_network_event_outbox` 增加 nullable `occurrence_id` 外键。
- 将 outbox 的 event_id 唯一索引改为普通索引，新增 occurrence_id 条件唯一索引。

迁移先回填再切换查询，支持滚动发布。Downgrade 在存在同一 Event 多条 outbox 时只保留最早一条后恢复 event_id 唯一约束，属于有损降级，发布前必须备份。

## 5. 发布门禁

- PostgreSQL 集成测试：累计文件 1/2/3 blocks + final replay。
- Contract v1/v2 历史回放和 missing exact version。
- 0027 数据样本升级到 0028 后 opening occurrence 数量核对。
- Celery/RabbitMQ 或 Redis broker Worker 强杀 redelivery。
- 双 Publisher 验证 occurrence outbox 的 at-least-once 与 Consumer 幂等。
- Device Timeline 覆盖 direct/workflow/历史 opening Event。
- 完整 `tests/unit tests/auth`、Ruff、migration current/heads。

自动化测试能证明确定性与事务语义；真实 Broker 强杀、Redis 断连和双 Publisher 竞争仍是生产发布前的外部故障注入门禁。

## 6. 实施结果（2026-07-11）

四个阶段的代码与数据迁移均已完成：

- 在线 Snapshot 使用稳定 command fact fingerprint，并由该 fingerprint 派生 deterministic UUID；累计报告 hash 仅保留为 provenance。
- progress persistence 失败写入 manifest 后继续采集，final callback 和任务 retry 可安全重放。
- Normalizer 按 Snapshot 的 `contract_version` 精确解析，版本缺失时以 `contract_version_unavailable` fail closed。
- 0028 已从 0027 实际升级成功；历史 Event 均有 opening occurrence，现有 Outbox 均绑定 occurrence。
- patrol task 已启用 late ack 和 worker-lost reject；redelivery 原子废弃旧 attempt 并分配新 attempt。
- Device Timeline 由设备 Snapshot/PatrolRun 驱动，支持 direct patrol，并兼容无 occurrence 的历史 opening Event。
- Event opening、recurrence、recovery 均按 occurrence 写独立 Outbox；ChangeDecision 已批量 upsert。
- devices_api 已清除已知用户可见 mojibake，并有扫描测试防止回归。

验证结果：

- PostgreSQL 累计报告测试：1/2/3 blocks + final replay 最终为 3 Snapshot、3 Evidence。
- 0028 数据核对：`events_without_occurrence=0`，`outbox_without_occurrence=0`。
- 0028 upgrade/downgrade 离线 SQL：生成成功；数据库 current/head 均为 `0028_patrol_replay_integrity`。
- Ruff、format、compileall：通过。
- `pytest tests/unit tests/auth -q`：`511 passed, 1 failed`。
- 唯一失败仍为既有 `test_delete_plugin_removes_directory`，其 workflow 插件注册表问题不属于本次巡检改造。
- `alembic check` 仍报告 0028 之前已存在的索引、唯一约束和 server default 漂移；未报告本次新增 occurrence/outbox 模型缺失。

代码与数据库迁移达到合入条件；真实 Celery Worker 强杀 redelivery、双 Publisher 竞争和 Consumer 幂等仍必须作为发布前外部故障注入门禁执行。
