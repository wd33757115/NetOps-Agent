# 巡检链路最佳实践审查与整改记录

## 1. 结论

整改前的巡检主链路能够在理想环境下运行，但不满足大规模生产巡检的完整工程要求。主要风险集中在并发控制失效时继续登录设备、批次先执行后留痕、历史报告读取可变事件、报告任务放大、原始输出归档边界和运行期规则版本漂移。

本次整改后，主链路调整为：

```text
预检与不可变执行计划
  -> 持久化批次与设备目标
  -> 可恢复的异步幂等分发
  -> 全局 SSH 租约控制
  -> 原始 CLI 采集与白名单归档
  -> Parser / Contract / Policy / Rule 运行清单冻结
  -> StateObservation
  -> Change
  -> EventOccurrence
  -> 批次终态单次报告
```

在 PostgreSQL、Redis、Celery Worker、Celery Beat 和对象存储按部署要求运行的前提下，该设计已符合当前项目面向千台级巡检的主要工程最佳实践。规则资产覆盖率和报告业务内容质量仍属于数据资产建设问题，不能由调度可靠性替代。

## 2. 核心设计不变量

1. 未持久化批次、目标设备和执行计划前，不允许启动设备 Workflow。
2. Redis 不可用或 SSH 租约丢失时，生产巡检停止新增登录和后续命令执行。
3. 同一批次、同一设备使用确定性 Workflow Run ID 或 Celery Task ID。
4. 同一目标在并发分发器之间只能被一个租约持有者认领。
5. 原始输出归档只包含本次明确返回的文件及其 `.collection.json` 内部清单。
6. Parser、ObservationContract、ChangePolicy 和 EventRule 在采集开始时冻结并生成摘要；后续步骤不得重新读取另一组 active 资产。
7. 历史事件和报告以追加写入的 EventOccurrence 为事实来源，不以可变 NetworkEvent 聚合状态改写历史。
8. 最终报告只在批次所有设备进入终态后原子申请一次。
9. 缺少分析规则不得阻断原始 CLI 采集；采集、归一化、变化检测和事件判定分别表达就绪度。
10. 所有降级、跳过、无事件和交付物不可用状态必须显式返回，不允许静默成功。

## 3. 已完成整改

### 3.1 并发与故障语义

- `PATROL_CONCURRENCY_FAIL_CLOSED=true` 为默认值。
- Redis 信号量不可用时抛出平台不可用错误，不再退化为进程内并发。
- 每条 CLI 命令执行前校验租约；续租失败后不再继续发送命令。
- 保留显式配置的非生产兼容模式，但生产默认 fail closed。

### 3.2 批次持久化与异步分发

- API 先创建执行计划、`PatrolBatchRecord` 和 `PatrolBatchDeviceRecord`，再投递分发任务。
- 批次和设备增加 dispatch 状态、尝试次数、认领令牌、租约、计划 ID 和最终 Run/Task ID。
- 分发按最多 500 条的小批次使用数据库行锁和 `SKIP LOCKED` 认领。
- Workflow Run、Workflow Step 和直接任务使用确定性 ID；Workflow Step 运行采用原子状态认领。
- Celery Beat 每 15 秒重扫 pending/stale 批次，API 投递失败不会丢失批次意图。
- 页面返回“批次已创建、等待后台分发”，不再把排队误报为已经启动。

### 3.3 原始输出与交付物

- 输出目录加入微秒时间和 capture UUID，避免同秒同设备覆盖。
- ZIP 使用文件白名单，不再打包设备目录中的其他历史文件。
- 对象存储不可用时归档状态为 `unavailable`，不再标记 `completed`。
- 上传成功后只清理已上传文件；失败文件保留 72 小时用于排错。
- 每小时清理带 sidecar 清单的过期采集文件，不删除无清单的其他文件。

### 3.4 运行期资产一致性

- 新增带 SHA-256 摘要的 `runtime_asset_manifest`。
- 清单包含 Parser Template 引用、ObservationContract、ChangePolicy、EventRule 和分阶段就绪度。
- Change Detector 和 Event Builder 校验清单摘要并只使用清单中的资产。
- State Normalizer 继续使用 Snapshot 已绑定的 Contract 版本。
- 缺 Contract/Policy/Rule 表达为分析降级，不阻断原始数据采集。

### 3.5 历史事件与报告

- 批次摘要、事件 CSV 和报告缓存指纹改为 occurrence-first。
- CSV 增加 `occurrence_id`、`occurrence_kind` 和当次 `status`。
- 旧数据仅在不存在 occurrence 时回退到 legacy NetworkEvent。
- 报告任务通过 manifest 获取全部 Workflow Run，移除 1000 Run 截断。
- 仅在所有目标 Run 终态后申请一次最终报告，并记录 queued/generating/completed/failed。
- Celery Beat 每 30 秒扫描符合终态条件但尚未申请报告的批次；报告任务入队失败时恢复为 `not_requested`，避免永久停留在伪 `queued` 状态。
- Markdown、DOCX、JSON 报告下载增加 admin/operator 权限校验。

## 4. 数据状态约定

### 批次分发

```text
pending -> dispatching -> completed
                      -> partial
```

设备目标状态：

```text
pending -> dispatching -> dispatched
                      -> failed -> retry
skipped 为预检终态
```

### 报告生成

```text
not_requested -> queued -> generating -> completed
                                     -> failed
```

### 分阶段就绪度

- `collection`：是否形成可执行采集计划；Parser 缺口作为数据质量诊断保留。
- `normalization`：结构化模板是否具有活动 ObservationContract。
- `change_detection`：相关 observation type 是否具有活动 ChangePolicy。
- `event_evaluation`：是否存在冻结的活动 EventRule 集合。

## 5. 数据库迁移

- `0044_patrol_batch_dispatch`：批次/目标分发状态、租约、计划关联、报告状态和 Workflow Step 唯一约束。
- `0045_patrol_batch_report_state`：报告失败详情。

本地 PostgreSQL 已升级到 `0045_patrol_batch_report_state`。其他环境发布前必须执行：

```powershell
venv\Scripts\alembic.exe upgrade head
```

## 6. 部署要求

- API、Celery Worker 和 Celery Beat 必须同时部署；Beat 每 15 秒向独立 `netops.health` 队列投递调度哨兵，诊断接口超过 45 秒未收到心跳即判定不可用。
- 生产启动脚本必须显式启用 Compose `core` profile，冒烟门禁同时检查 `celery-patrol`、`celery-control`、`celery-beat` 容器及 `celery_beat` 调度心跳。
- 巡检 Worker 必须监听 `netops.patrol` 队列。
- Redis 不可用时生产巡检预期失败关闭，需要平台告警，而不是临时放宽并发。
- MinIO 不可用时原始文件在本地保留 72 小时，恢复后需重新执行或人工取证。
- 应监控 `dispatch_status=partial`、`report_status=failed`、租约丢失和归档 `unavailable`。

## 7. 验证记录

- 巡检、状态、事件、报告和设备 API 回归：258 passed。
- PostgreSQL/Redis/Celery 故障注入：13 passed。
- 前端 Vitest：36 passed。
- 前端 `vite build`：通过，3364 modules transformed。
- Ruff（本次相关文件，忽略仓库既有超长行）：通过。

## 8. 后续验收重点

本次整改解决的是链路正确性和可靠性。发布前仍需在预发布环境使用真实设备或 `lixian` 数据完成以下业务验收：

1. 千台级批次的分发速率、SSH 并发上限和报告生成耗时。
2. 不同厂家/型号 Parser、Contract、Policy、Rule 的实际覆盖范围。
3. 光衰阈值优先采用设备/模块输出，缺失阈值时明确标记 `threshold_unknown`。
4. 甲方报告中的网络现状、风险分级、数据完整性和不确定性说明。

只有规则资产覆盖和报告业务验收同时通过，才能将“链路可运行”升级为“巡检结论可信”。
