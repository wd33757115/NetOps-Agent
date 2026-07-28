# TextFSM Generator 使用说明

## 1. 功能定位

`textfsm-generator` 只负责从已确认的 Parser Job 生成、验证并登记 TextFSM 候选资产。
生产执行的唯一输入是 `job_id`。离线巡检导入、聊天触发和 Parser 缺口扫描只负责创建 Job
和关联样本，不能直接生成、发布或激活生产模板。

```text
离线样本或生产缺口
-> Parser Job
-> LLM 自动 ParserSpec 与记录方案
-> DeepSeek thinking 工具循环（每次运行最多三次模型调用）
-> 本地硬门禁与独立 LLM 结果复核
-> pending_approval
-> 人工确认最终结果与主键
-> Active Parser
```

## 2. 输入

```json
{
  "job_id": "parser-job-123",
  "publish": false,
  "dry_run": true
}
```

- `job_id`：必填，必须是 `simple_v2` Job。
- `publish`：是否登记待审批候选；默认 `false`。
- `dry_run`：只生成验证报告，不登记候选。
- `templates_dir`、目录直读、聊天正文和数据库直扫等旧参数已停止支持。

## 3. 样本与 ParserSpec

样本分为三类：

- `generation`：确定性编译器和 LLM 可见的脱敏生成样本。
- `acceptance`：可选的独立验收样本，不进入 LLM Prompt，也不用于循环修复。
- `negative`：权限错误、截断、空输出等不得产生业务记录的样本。

一份 `generation` 样本即可生成候选并进入人工审批。没有独立 `acceptance`
样本时，候选记录为 `generation_sample_only`，审批工作台必须提示工程师逐项核对
原始输出、TextFSM 模板和解析结果；存在 `acceptance` 样本时仍必须全部通过。

ParserSpec 和逐样本记录方案由 LLM 从脱敏样本自动生成并采纳。工程师可在高级修订中调整
实体、主键、字段、记录范围、样本角色和可选验收基准，但默认不需要手写 JSON。自动链路
不得根据厂商、型号、命令或业务字段增加 Python 硬编码分支。

## 4. 生成与验证

生产默认直接进入受限的 DeepSeek thinking 工具循环。模型通过
`validate_textfsm_candidate` 提交完整模板，平台在本地执行编译、解析和硬门禁，
再将结构化结果回传模型；每次明确生成运行最多调用三次。工具循环不接触 acceptance 样本，
也不能修改 ParserSpec 或直接发布模板。

候选必须通过：TextFSM 编译、正样本解析、必填与类型、主键非空唯一、字段值首尾空白规范、
可选预期基准、负样本零记录、独立 LLM 完整性/主键复核和不可变摘要一致性。任一门禁失败
都不能进入审批。三个确定性编译器只作为显式人工/测试回退接口，不定义生产验收真值。

## 5. 资产存储与审批

新候选的模板正文、SHA-256、ParserSpec、样本摘要和验证报告固化在 PostgreSQL。Worker 不写
`src/skills/shared/textfsm-templates/` 或其他本地运行时目录。历史 Active 本地模板仅保留只读
运行时兼容。

审批工作台默认展示：

- 每份样本的原始 CLI 和最终解析结果。
- 最终记录数和主键字段。
- “记录完整且字段正确”与“主键稳定唯一”两项显式确认。

TextFSM、ParserSpec、记录方案、门禁、独立复核和历史尝试在次级页签中保留。只有证据完整、
全部门禁通过且两项人工确认完成时才能激活。拒绝、激活、回滚均记录操作人和原因；被驳回
候选可直接重新生成，每次运行恢复三轮模型预算，但历史调用流水不删除。

## 6. 输出

```json
{
  "success": true,
  "mode": "job",
  "job_id": "parser-job-123",
  "candidate_groups": 1,
  "success_count": 1,
  "published_count": 0,
  "validation_pass_rate": 1.0,
  "asset_store": "postgresql"
}
```

## 7. 运行时边界

- 正式巡检只读取 Active Parser，不在运行时调用 LLM。
- Parser 激活后独立触发 ObservationContract、ChangePolicy、EventRule 候选生成。
- 规则资产 LLM 只能使用脱敏 `structured_json`，不得读取原始 CLI。
- 手工内置 Parser/Rule Provider 接口继续保留，用于兼容、测试和受审计的紧急兜底。
- 历史 Agent Job 和生成尝试只读保留，不能调度或重试；补齐 ParserSpec 和样本角色后创建
  Simple v2 Job。

完整设计与迁移状态见 `docs/simple-parser-pipeline-v2-2026-07-22.md`。
