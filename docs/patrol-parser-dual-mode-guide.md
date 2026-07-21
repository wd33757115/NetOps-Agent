# 巡检执行与规则资产生产双链路手册

## 1. 数据与职责边界

巡检 Snapshot、Dataset、Parser 任务、状态、变化和事件统一存储在 PostgreSQL。
浏览器上传的原始 CLI 保存在 MinIO 私有目录，并通过 `FileMetadata` 进行所有权和访问控制。

| 模式 | 用途 | Snapshot 数据面 | 后续处理 |
|---|---|---|---|
| `parser_validation` | 在 Parser 控制面收集和验证新设备、新版本、新输出格式 | PostgreSQL validation Dataset | 可创建或修复 Parser/规则候选，不发布正式事件 |
| `production` | 设备巡检页发起正式巡检 | PostgreSQL production Dataset | 只读 active 资产，生成 State、Change、Event、Report |

正式巡检遇到 `template_missing` / `parse_failed` 时：

- **不会**创建 Parser LLM 候选任务；
- 在开启 `PRODUCTION_GAP_LEDGER_ENABLED` 时写入 `netops_parser_gap_ledger`；
- Beat 任务 `promote_parser_gap_ledger` 可把达到阈值的缺口提升为 **pending draft** Parser Job（`source_mode=production_gap`），仍不自动 dispatch。

旧 SQLite 文件不是运行数据源。`file_path` 仅作为管理员在服务器本机执行的兼容入口，
普通用户应通过 Parser 控制面的“资产生产”工作区上传文件。

## 2. 两套操作入口

### Skills / Parser 控制面

访问 `/skills/parser?workspace=production&subtab=source-jobs`，用于：

- 创建单周期资产样本任务；
- 创建双周期规则回放样本任务；
- 上传多文件、文件夹或 ZIP；
- 预览设备身份、命令、Parser 状态和质量门禁；
- 确认 Inventory 设备绑定；
- 按命令查看 Parser、Contract、Policy、EventRule 的十阶段生产进度；
- 进入生成、工程复核、审批发布和运行时覆盖工作区；
- 下载样本原始输出、回放变化、回放事件和规则回放报告。

任务列表采用服务端分页，支持搜索、用途、状态和归档筛选。

### 设备巡检页

访问 `/devices?tab=patrol`，用于正式巡检，包含“在线巡检”和“离线巡检”：

- 发起设备巡检批次并查看设备执行状态；
- 只读取 active Parser、ObservationContract、ChangePolicy 和 EventRule；
- 展示 State、Change、Event、证据链和正式巡检报告；
- 离线巡检导入历史 CLI，但只读取 active Parser、Contract、Policy 与 EventRule，不创建候选资产；
- 遇到缺失资产时记录降级原因，但不创建或启动规则生成任务。

新规则不会由离线导入代码硬编码。手工 JSON 和内置 Provider/Registry 仍作为兼容、测试及
紧急兜底接口保留。

## 3. 单周期解析验证

```text
浏览器上传原始 CLI
  -> OfflinePatrolJob(parser_validation)
  -> 异步预览（不写 Snapshot）
  -> 设备绑定确认
  -> parser-validation-ingest
  -> patrol-raw-ingestor(mode=parser_validation)
  -> PostgreSQL Dataset / Snapshot
  -> CREATE_TEMPLATE 或 REPAIR_TEMPLATE 候选
  -> textfsm-generator
  -> 验证、审批、激活
```

允许出现 `template_missing` 和 `parse_failed`，因为该模式的目的就是发现和修复 Parser。
预览不会写 Snapshot，也不会创建候选任务；正式执行才会持久化。

## 4. 双周期规则回放

baseline 的采集时间必须早于 current。执行流程为：

```text
执行前质量门禁
  -> 导入 baseline
  -> State Normalizer(update_current_state=false)
  -> 导入 current
  -> State Normalizer(update_current_state=false)
  -> Change Detector
  -> Event Builder
  -> Rule Replay Report
```

固定 `publish_events=false`，事件只作为候选规则的历史回放结果，不能发布到正式事件流。

以下情况会阻断执行：

- 文件损坏、哈希不符或没有可识别命令；
- baseline 时间不早于 current；
- 两个周期没有共同设备；
- 关键结构化命令缺模板、解析失败或身份不明；
- 比较字段缺少 active Contract、ChangePolicy 或 EventRule；
- 设备匹配存在未确认歧义。

## 5. 幂等与重试

任务创建和 Workflow 执行均使用幂等键。每次重试生成新的 attempt，并使用确定性的
PatrolRun ID：

```text
{offline_job_id}-{period_role}-attempt-{attempt_number}
```

Snapshot 使用源文件哈希、设备、规范化命令和原始输出哈希生成导入指纹。重复上传或重试
不会重复写入相同 Snapshot。一个周期的所有文件共用一个 PatrolRun，全部文件处理完成后
统一结束该 Run。

## 6. LLM 上下文安全

规则生成上下文只查询以下 Snapshot 投影：

```text
dataset_id
device_id
observed_at
structured_json
```

服务不会读取或发送 `raw_text`。上下文还会执行：

- 字段白名单和敏感键移除；
- 设备名、管理 IP、邻居 IP 的稳定假名化；
- 多 Dataset 轮询采样；
- 数值字段 `historical_values` 自动生成；
- context digest 和隐私检查报告持久化。

生成 Workflow 使用不可变 `context_id`。上下文存在敏感数据、缺少有效 Parser 或历史回放
存在碰撞时，不允许审批激活。

## 7. API

规则资产样本入口：

```text
POST   /api/v1/parser/asset-source-jobs
GET    /api/v1/parser/asset-source-jobs
GET    /api/v1/parser/asset-source-jobs/{job_id}
POST   /api/v1/parser/asset-source-jobs/{job_id}/sources
POST   /api/v1/parser/asset-source-jobs/{job_id}/preview
POST   /api/v1/parser/asset-source-jobs/{job_id}/device-bindings/confirm
POST   /api/v1/parser/asset-source-jobs/{job_id}/execute
POST   /api/v1/parser/asset-source-jobs/{job_id}/cancel
POST   /api/v1/parser/asset-source-jobs/{job_id}/retry
POST   /api/v1/parser/asset-source-jobs/{job_id}/archive
GET    /api/v1/parser/asset-source-jobs/{job_id}/artifacts/{kind}
```

离线巡检使用独立的 `/api/v1/patrol/offline-jobs`。两类任务共享上传、预检和文件证据基础设施，但通过 `purpose` 隔离，不能从一个工作区读取或执行另一个工作区的任务。

规则生成入口继续使用 Parser 控制面：

```text
POST /api/v1/parser/rule-generation-jobs/{job_id}/generation-context
POST /api/v1/parser/rule-generation-jobs/{job_id}/start-generation
```

请求可传 `dataset_ids` 生成不可变上下文，或传已有 `context_id`。手工
`context_overrides` 保持兼容，但不可覆盖不可变上下文中的 `records` 和
`historical_values`。

## 8. 本地验收数据

完整本地验收使用 `lixian` 目录：

| 周期 | 文件 | 设备 | 命令 |
|---|---:|---:|---:|
| Q1 | 2 | 2 | 54 |
| Q2 | 2 | 2 | 44 |
| Q3 | 2 | 2 | 54 |

预览应识别 SW1/SW2、管理 IP、接口状态和光衰命令。完整目录只用于本地或预发布 E2E；
CI 使用从中提取的最小脱敏样本。
