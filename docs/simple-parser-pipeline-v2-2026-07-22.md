# Simple Parser Pipeline v2 实施边界

## 1. 决策

Parser 资产生产收敛为一条以 `job_id` 为唯一生产入口的有限流程：

```text
Parser Job
-> 不可变样本集
-> LLM 自动语义方案（ParserSpec + 记录方案）
-> DeepSeek thinking 工具循环（最多三次模型调用）
-> 本地客观硬验证
-> 独立 LLM 结果复核
-> 候选模板
-> 人工确认最终解析结果与主键
-> Active Parser
```

目录扫描、离线文件导入、聊天触发和数据库缺口扫描只负责发现缺口、创建 Job、关联样本，不再直接生成或发布生产模板。Parser 激活后再独立触发 ObservationContract、ChangePolicy 和 EventRule 的候选生成，避免规则资产需求反向增加 TextFSM 生成复杂度。

## 2. 必须保留的工程能力

- 分层样本验证：一份生成样本即可形成候选，验收样本和负样本存在时职责保持分离。
- 明确字段与主键：LLM 自动形成精简 `ParserSpec`，驱动字段声明、类型、必填和主键校验；
  工程师保留定向修订入口，但默认不从空白 JSON 开始配置。
- 不可变资产：候选模板内容、摘要、样本摘要和验证结果一并固化。
- 人工审批与审计：未经审批的候选不得进入运行时；激活、拒绝、回滚均保留操作者和原因。
- 手工内置 Parser/Rule Provider 接口：仅用于兼容、测试和受审计的紧急兜底，不作为新资产的默认生产路径。

## 3. 生成与验证边界

生产默认由 LLM 直接生成完整 TextFSM。`table`、`key_value`、`repeated_block` 三个结构编译器
仅保留为显式的人工/测试回退能力，不再作为生产默认入口，也不得反向推导验收真值。

平台先让 LLM 根据脱敏样本形成 ParserSpec 和逐样本记录语义方案，自动采纳后进入受限的
DeepSeek thinking 工具循环。模型生成完整模板并调用 `validate_textfsm_candidate`，平台在本地
编译、解析和执行硬门禁，再将结构化诊断作为 tool result 回传模型。候选通过本地门禁后，
由一个全新会话的独立 LLM 复核原始证据与最终记录；复核失败同样形成结构化修复意见并回到
生成会话。每次明确生成运行最多三次模型调用；LLM 不得发布模板或无限循环尝试。

工具循环遵循以下边界：

- 请求显式启用 thinking，并传入工具定义，但不强制 `tool_choice`。
- 发生工具调用后，下一次请求完整回传上一轮 assistant 的 `reasoning_content`、`content` 和 `tool_calls`。
- 模型只能调用本地 TextFSM 校验或请求工程师复核，不能执行 Shell、Python、数据库或文件系统操作。
- generation 与 negative 样本参与循环；acceptance 样本只做最终独立验证，结果不回灌模型。
- 模型未调用工具但返回完整模板时，平台执行同一套本地验证作为兼容兜底。

生产候选至少通过以下硬门禁：

1. TextFSM 语法可编译。
2. 至少一份生成样本可解析；存在独立验收样本时必须全部可解析。
3. 每条记录的必填字段完整且类型有效。
4. 主键非空、唯一且可规范化。
5. 字段值不得保留 CLI 排版产生的首尾空白。
6. 若工程师设置了可选验收基准，记录数量和预期主键集合必须一致。
7. 负样本不产生业务记录。
8. 独立 LLM 对记录范围、字段对应和主键稳定性的复核通过。
9. 候选内容与不可变资产摘要一致。

系统不得使用“被测模板本身的解析结果”生成该模板的验收基准。预期记录数与主键集合是
可选的人工增强证据；未配置时由最终结果人工确认和冻结的审批基线承担回归职责。

`acceptance_samples` 是可选的独立验证证据，不进入 LLM Prompt，也不用于循环修复。
仅有一份 generation 样本时允许生成候选并进入人工审批，候选必须标记为
`generation_sample_only`；审批人核对原始输出、模板和解析结果后可以激活。
如果提供了 acceptance 样本，则其失败仍会阻断审批，不能退化到单样本模式绕过。

## 4. 人工审批工作台

默认审批界面只要求审批人在同一工作台核对：

- 每份命令样本的原始 CLI 与最终结构化解析结果。
- 记录是否完整、字段是否对应正确。
- 主键是否能稳定且唯一地标识记录。

TextFSM 全文、ParserSpec、逐样本记录方案、硬门禁、独立复核和尝试审计保留在同一抽屉的
次级页签中，供工程师诊断和定向修订，但不再成为日常审批的前置填表工作。

只有全部审批证据存在、所有样本通过且资产摘要一致时，界面才允许发起激活。原始 CLI 仅用于受权限控制的人工审批，不得进入 ObservationContract、ChangePolicy 或 EventRule 的 LLM 上下文；规则资产上下文只能使用脱敏后的 `structured_json`。

本轮已落地上述审批证据 API 和审批工作台，替换原先只展示结果摘要的激活抽屉。

## 5. 渐进迁移

### Phase 1：收紧生产入口

- 新模板生产必须绑定 `job_id`。
- 非 Job 入口只创建任务和样本，不直接发布。
- AI Worker 只写不可变候选资产，不写运行时本地模板。
- 使用旧 Active 模板作为只读回归基线，不保留旧生成算法入口。

### Phase 2：建立 SimpleParserGenerationService

- 引入精简 `ParserSpec` 和三类结构编译器。
- 将生成编排收敛为普通有限状态流程。
- 模型调用预算固定为最多三次连续 thinking/tool 调用。
- 建立 generation、acceptance、negative 三类样本清单及预期主键/记录数。
- 新 Job 固定使用 `simple_v2`；历史 Job 需经工程师确认 Spec 和样本角色后升级。

### Phase 3：迁移和删除旧复杂度

- 用离线金样本和生产候选执行新旧双跑，比较通过率、字段完整性和人工干预率。
- 当新链路覆盖主要布局并连续通过回归后，停止旧 LangGraph/Contract revision 主路径接收新任务。
- 最后删除重复 Provider、无效模式分支和重复 Validator；资产版本、审批、回滚、审计继续保留。

## 6. 完成标准

- 任一生产候选均可从 `job_id` 追溯到样本、ParserSpec、生成尝试、验证和审批记录。
- 低能力模型无法通过重复调用绕过硬门禁；失败必须产生明确诊断和人工下一步。
- 审批人能逐样本核对原始 CLI、解析结果和精确模板，无法在证据不完整时激活。
- Parser 激活与规则资产生成解耦，巡检运行时只读取 Active Parser 和 Active Rule Asset。
- 新链路达到既定覆盖率后，旧复杂链路能够被可验证地关闭，而不是长期双轨维护。

## 7. 当前实施状态（2026-07-22）

### Phase 1：已完成

- 生产生成只接受 `job_id`，目录、聊天和数据库扫描不能直接生成候选。
- 新任务默认使用 `simple_v2`，旧 Agent 不再接收新任务；历史任务保留只读审计。
- Worker 只写不可变候选并登记 `pending_approval`，不会自动激活。
- 新候选模板正文和 SHA-256 摘要固化到 PostgreSQL，Worker 不写运行时本地模板文件；
  历史 Active 本地模板仅保留只读兼容。

### Phase 2：已完成

- 已实现 LLM 自动 ParserSpec、逐样本记录语义方案和 generation/acceptance/negative 三类样本。
- 预期记录数/主键集合改为可选人工增强证据，不再由被测 Parser 反向生成。
- 生产默认走 LLM；`table`、`key_value`、`repeated_block` 仅保留为显式回退接口。
- 已实现 DeepSeek thinking 工具循环、每次明确生成最多三次模型调用及九项发布门禁。
- 已实现 `deepseek-thinking-tools-v1` 协议：完整回传工具调用轮次的
  `reasoning_content`，本地执行校验，并保存每轮候选、耗时、Token、失败签名和父子尝试关系。
- 已实现独立 LLM 结果复核；失败诊断回送生成会话，复核通过证据进入不可变候选。
- 控制面默认自动采纳语义方案并自动调度；高级修订保留 ParserSpec、记录方案、样本角色和
  可选验收基准编辑能力。
- 审批工作台默认展示原始 CLI、最终解析结果和主键；TextFSM、过程资产与审计证据作为次级页签。
- 显式重试按每次运行重置三轮预算，同时保留全局递增调用流水号和全部历史。

### Phase 3：已完成

- 旧确定性编译器迁移基线曾使用 `lixian` Q1/Q2/Q3，覆盖 4 个型号、3 个金样板命令、
  12 个资产组、42 份样本和 1,214 条实体记录；当时七项门禁全部通过且 LLM 调用次数为
  0。该结果仅用于证明旧代码移除时没有解析回退，不代表当前生产生成策略。
- 当前生产链路以 LLM 生成、客观本地校验和独立 LLM 复核为主，并执行本文定义的九项
  发布门禁。
- 对已有 3 个 Active 金样板模板完成值级双跑：记录数、主键集合、共享字段值和字段集合
  均无回退，报告输出 `legacy_code_removal_ready=true`。
- 已删除旧 Parser LangGraph、Contract revision 执行入口、八 Provider Registry、旧合成器、
  重复 Validator，以及目录、聊天和数据库直接生成分支。
- 已删除新旧链路 Feature Flag；应用和数据库对新 Job 的默认值都固定为 `simple_v2`，迁移头为
  `0053_parser_embedded_content`。
- 历史 `legacy` Job 和旧尝试记录继续作为只读审计证据展示，但不能调度或重试；确认
  ParserSpec 和样本角色后才可升级为 Simple v2。
- Parser 专项测试 `157 passed`，全量后端单元测试 `872 passed`；前端 `53 passed`，
  ESLint 与生产构建通过。
- 规则资产链路的 ObservationContract、ChangePolicy、EventRule Agent 不属于旧 Parser Agent，
  已完整保留并继续通过离线结构化数据触发。

迁移验收报告位于：

```text
.tmp/simple-parser-lixian-benchmark/simple-parser-lixian-benchmark.json
```
