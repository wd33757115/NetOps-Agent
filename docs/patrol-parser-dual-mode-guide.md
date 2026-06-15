# 巡检与 Parser 双模式使用手册

## 1. 运行模式

| 模式 | 用途 | Snapshot 数据库 | 模板候选 |
|---|---|---|---|
| `parser_validation` | 收集和验证新设备、新版本、新输出格式 | `src/skills/shared/databases/parser-validation.db` | 可创建 CREATE/REPAIR 任务 |
| `production` | 正式巡检、变化检测和事件构建 | `src/skills/shared/databases/patrol.db` | 禁止创建模板任务 |

设备清单由 `device-patrol` 和 `device-backup` 共用：

`src/skills/shared/databases/devices.db`

三个 SQLite 文件均为本地私有运行数据，不应提交到 Git。

## 2. 解析验证流程

```text
测试设备或离线巡检文件
  -> patrol-raw-ingestor(mode=parser_validation)
  -> parser-validation.db
  -> ParserPolicy / Identity / Active Template
  -> CREATE_TEMPLATE 或 REPAIR_TEMPLATE 候选
  -> textfsm-generator(job_id)
  -> draft 模板
  -> 审批并激活
```

推荐通过 `parser-validation-ingest` Workflow，或直接调用：

```json
{
  "file_path": "C:\\patrol-samples",
  "mode": "parser_validation",
  "run_id": "parser-validation-2026q2"
}
```

命令不在结构化白名单时为 `ignored`，不会创建模板任务。身份不可靠时为 `identity_unknown`，也不会调用 LLM。

## 3. 生成模板

生产模板只能来自验证库中的控制面 Job：

```json
{
  "job_id": "<parser_template_job_id>",
  "minimum_sample_pass_rate": 1.0,
  "publish": true
}
```

Generator 生成并验证 draft，但不直接激活。目录和直接输入只用于发现、聚合与验证：

```json
{
  "source_path": "C:\\patrol-samples",
  "recursive": true,
  "publish": false
}
```

如果 Job 的任一样本不来自 `parser-validation.db`，Generator 会拒绝执行。

## 4. 正式巡检流程

```text
devices.db 中的 vendor + model
  -> Parser 覆盖率预检
  -> 通过后连接设备并采集
  -> patrol.db
  -> change-detector
  -> event-builder
```

正式模式遇到以下情况会报错，且不会触发 Generator：

- 应结构化命令缺少 active 模板；
- 设备 `vendor/model` 不完整；
- 模板文件缺失或哈希不一致；
- active 模板解析失败或返回非法空结果。

调用示例：

```json
{
  "filter_params": {"device_name": "PE01"},
  "mode": "production",
  "save_baseline": false
}
```

标准 `patrol-change-event` Workflow 已固定使用 `production`。

## 5. 设备库

设备表分别保存厂商和精确型号：

```text
vendor = H3C
model  = S5130S-54S-HI-G
```

不要合并为 `H3C S5130S-54S-HI-G`。现有数据库首次打开时会自动增加 `vendor` 列。

测试初始化：

```powershell
.\venv\Scripts\python.exe src\skills\device-backup\scripts\init_test_db.py
```

## 6. 查看、审批和激活

```text
GET  /api/v1/parser/jobs
GET  /api/v1/parser/templates
GET  /api/v1/parser/templates/{template_id}/preview
POST /api/v1/parser/templates/{template_id}/activate
POST /api/v1/parser/templates/{template_id}/reject
```

模板预览是只读操作，展示 TextFSM 内容、SHA-256 校验结果和生成时的验证报告。

## 7. 故障处理

`production patrol blocked by parser coverage preflight`：
先用验证模式采集同型号、同版本样本，完成模板生成、审批和激活。

`identity_unknown`：
检查 `devices.db` 的 `vendor/model`，或补充
PostgreSQL 中已确认的 `parser_device_profiles` 和
`parser_identity_signatures`。首次未知型号只允许在 parser-validation
模式通过通用厂商指纹和版本输出发现，审批模板后再固化身份资产。

Generator 拒绝数据库：
确认 Job 样本来自
`src/skills/shared/databases/parser-validation.db`。

PostgreSQL 不可用：
Parser 控制面是强依赖，应先恢复数据库连接，巡检不会绕过控制面运行。

## 8. 数据边界

- `devices.db`：设备资产和凭据。
- `parser-validation.db`：模板研发样本，不参与正式变化分析。

## 数据集、幂等与预览

测试巡检导入支持 `dataset_id`，建议按季度或测试批次命名，例如
`xian-2025Q3`、`xian-2025Q4`。Parser 控制面的“数据集覆盖率”页面按数据集展示：

- 结构化覆盖率；
- 缺模板率；
- 解析失败率；
- 合法空结果率；
- 主键稳定率。

写入前应先执行导入预览。预览只读取和解析文件，不创建 Snapshot 或模板任务，
并返回预计新增、重复、模板就绪、缺模板、解析失败、忽略及身份未知数量。

Snapshot 导入指纹由以下内容生成：

```text
源文件哈希 + device_id + canonical_command + 原始输出哈希
```

相同指纹再次导入时直接复用原 Snapshot，不会重复创建候选任务。

## 任务合并与自动退役

开放任务按以下维度合并：

```text
job_type + vendor + model/family + canonical_command + output_signature
```

新 Snapshot 追加到已有开放任务。重新使用共享测试库导入时，仍引用旧
`.runtime/patrol/control-plane-*.db` 的任务会标记为 `superseded`，默认不再调度。

## 数据契约与回归验证

每个模板版本同时固化数据契约及其 SHA-256：

```text
entity_type
primary_keys
required_fields
optional_fields
validators
allow_empty
empty_patterns
ignore_fields
```

生成器除验证触发任务的样本外，还会加载共享测试库中同厂商、同型号、同命令的
历史 Snapshot 作为回归集。候选模板必须通过全部回归样本才能激活。

模板激活前，控制面会展示当前 active 模板与候选模板在相同 Snapshot 上的：

- 解析状态；
- 结构化记录；
- 数据契约哈希变化；
- 结果差异。
- `patrol.db`：通过生产覆盖率门禁的正式 Snapshot。
- PostgreSQL：模板、任务、样本引用、解析版本和 Outbox 的权威控制面。
- `src/skills/shared/textfsm-templates/`：不可变模板文件。
