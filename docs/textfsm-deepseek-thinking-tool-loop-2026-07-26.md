# TextFSM DeepSeek Thinking 工具循环设计

## 1. 目标

将 TextFSM 生成收敛为一条以 Parser Job 为唯一入口、可验证、可恢复、
可审批的有限循环：

```text
冻结 Parser Job / ParserSpec / 样本清单
-> 尝试重新验证同输入下的历史有效候选
-> DeepSeek 生成完整 TextFSM，并决定提交本地校验
-> Python TextFSM 编译和解析
-> 本地硬门禁
-> 将真实解析结果和诊断回传同一模型会话
-> 最多三轮生成或修复
-> 独立语义复核
-> pending_approval
-> 工程师核对原始样本、最终解析结果、主键和模板
```

LLM 负责提出和修复候选，平台负责执行代码、保存证据和决定是否允许进入审批。
模型不能执行 Shell、Python、数据库或文件系统操作，也不能直接发布资产。

## 2. DeepSeek 协议约束

请求使用：

```python
client.chat.completions.create(
    model=settings.LLM_MODEL,
    messages=messages,
    tools=tools,
    reasoning_effort="high",
    extra_body={"thinking": {"type": "enabled"}},
)
```

约束如下：

- 不强制设置 `tool_choice`，由模型决定提交候选或请求工程复核。
- 不发送 thinking 模式不支持的采样参数。
- 工具调用后的下一轮请求完整保留上一轮 assistant 的
  `reasoning_content`、`content` 和 `tool_calls`。
- 每个 `tool_call_id` 后紧跟对应的本地工具结果。
- 本地工具只接收完整 `template_text`，不开放任意代码执行能力。
- `reasoning_content` 只存在于当前内存会话，不写数据库、不展示给用户。

## 3. KV Cache 设计

DeepSeek 上下文缓存是服务端自动能力，不需要也不允许平台伪造 cache key。
命中条件是请求拥有完全相同的前缀，因此消息按以下方式组织。

### 3.1 生成会话稳定前缀

稳定部分按固定顺序放在会话开头：

1. TextFSM 方言和工具协议。
2. ParserSpec。
3. 脱敏 generation samples。
4. 结构画像和字段证据。
5. 工具 schema。

动态内容仅追加在尾部：

- 当前候选；
- Python TextFSM 的真实解析结果；
- 失败门禁和首要诊断；
- 剩余轮次。

不得在稳定前缀中加入时间戳、随机 ID、不断变化的计数或无意义排序差异。

### 3.2 独立复核稳定前缀

复核器使用独立会话，稳定前缀只包含：

- ParserSpec；
- 冻结后的脱敏 CLI；
- 固定复核规则。

每轮候选模板和实际解析结果作为新的 user 消息追加。复核器保留自身上一轮
assistant 消息，因此后续候选可以复用相同证据前缀。

### 3.3 可观测性

每次调用记录：

- `prompt_tokens`；
- `completion_tokens`；
- `prompt_cache_hit_tokens`；
- `prompt_cache_miss_tokens`；
- 延迟；
- 模型、协议版本和候选 SHA-256。

缓存命中率是成本与延迟指标，不是正确性门禁。缓存失效不能改变生成结论。

## 4. 本地校验工具

`validate_textfsm_candidate` 执行：

1. 平台方言 lint。
2. `textfsm.TextFSM` 编译。
3. 对 generation 和 negative samples 执行 `ParseText`。
4. 必填字段完整性。
5. 主键非空和唯一性。
6. 字段类型和首尾空白检查。
7. 可选的人工验收基准检查。
8. 生成结构化失败诊断。

送给 LLM 的解析结果必须保留原始 JSON 类型：

- 字符串仍为字符串；
- 数字和布尔值不转换为字符串；
- TextFSM `Value List` 保持 JSON 数组；
- 只对超长字符串和预览记录数量做有界裁剪。

禁止通过 `str(value)` 把数组变成 `"['a', 'b']"`。这种转换会让复核器误以为
Parser 输出了 Python 字面量字符串。

## 5. Value List 语义

TextFSM `Value List` 是平台支持的正式输出。Python TextFSM 返回原生列表，
State Normalizer 会按顺序生成带一基 `value_index` 的观察值。

独立复核规则：

- 不能仅因为字段是 JSON 数组就判定字段错位。
- 必须检查数组元素是否完整、顺序是否正确、是否属于正确实体、是否有 CLI 证据。
- 对“重复业务值”和“物理换行片段”应在后续 ParserSpec 演进中增加显式
  `collection_mode`，避免依靠字段名或命令名硬编码判断。

当前审批页面仍需展示数组原貌，由工程师确认这种结构是否满足后续 State/Change
语义。需要拼接或集合归一化时，应通过字段级结构契约实现，不应在某个品牌命令的
业务代码中写特殊处理。

## 6. 有限循环

```text
历史候选存在且输入摘要一致
  -> 重新执行本地校验
  -> 独立复核
  -> 通过：直接进入待审批，0 次新生成调用
  -> 失败：进入模型生成循环

模型第 1 轮
  -> 本地通过 -> 独立复核
  -> 本地失败或复核失败 -> 精确诊断回传

模型第 2 轮
  -> 同上

模型第 3 轮
  -> 通过：进入待审批
  -> 失败：阻断并请求工程复核
```

历史候选只有在以下摘要完全一致时才可复用：

- ParserSpec SHA-256；
- generation sample set SHA-256；
- acceptance sample set SHA-256。

复用不是沿用旧结论。候选必须重新通过当前版本的 Python TextFSM、本地门禁和独立
语义复核，然后创建新的不可变尝试记录。

## 7. 预算和故障处理

- 每次明确生成运行最多三次候选生成调用。
- 可重试的网络或供应商连接错误允许一次独立 provider retry。
- Provider retry 不消耗候选生成预算，但调用流水永久保留并标记
  `budget_released=true`。
- 协议错误、模型主动请求工程复核和不可重试供应商错误立即阻断。
- acceptance samples 不进入 Prompt，失败结果也不回灌生成会话。
- 重试任务先复核已有候选；只有旧候选不再有效时才重新调用模型。

## 8. 审批证据

人工审批必须在同一工作台展示：

- 命令、厂家、型号和兼容范围；
- 每份原始命令样本；
- TextFSM 模板全文；
- Python TextFSM 的最终解析结果；
- 主键字段及逐样本主键集合；
- 本地硬门禁；
- 独立语义复核；
- 生成和复核调用的模型、耗时、Token 与缓存命中；
- 样本模式风险提示。

只有候选内容、样本摘要、ParserSpec 摘要和审批证据一致时才能激活。

## 9. `show vpc` 真实回放

2026-07-26 对 `cisco / NEXUS9000 / show vpc` 执行真实回放：

- 两份 generation samples；
- 每份解析 15 条 vPC 记录；
- 主键为 `id`，两份样本均稳定；
- 多行 `active_vlans` 保留为有序 JSON 数组；
- 本地硬门禁全部通过；
- 独立语义复核通过；
- 复核调用 cache hit 7296 tokens、cache miss 8 tokens；
- 失败任务重试复用了已有 LLM 候选；
- 新模板生成调用 0 次；
- 候选已进入 `pending_approval`。

本次回放同时证明：KV Cache 可以显著复用固定证据前缀，但真正避免重复生成的是
数据库中的不可变候选和输入摘要校验，两者职责不能混淆。

## 10. 参考

- [DeepSeek 思考模式](https://api-docs.deepseek.com/zh-cn/guides/thinking_mode/)
- [DeepSeek Tool Calls](https://api-docs.deepseek.com/zh-cn/guides/tool_calls/)
- [DeepSeek 上下文硬盘缓存](https://api-docs.deepseek.com/zh-cn/guides/kv_cache/)

## 11. 浏览器实测后的门禁修订

2026-07-26 使用生产工作台真实执行 `show ip interface brief` 和 `show version` 后，
原方案增加“样本资格预检”阶段：

```text
脱敏输出
  -> 确定性样本分类
  -> 有业务记录：进入 ParserSpec 与生成循环
  -> 仅有表头：阻断并要求补充非空样本
  -> 明确合法空/命令错误：作为负样本
```

分类规则必须满足：

- “合法空输出”只允许匹配完整状态行，不得在版权、免责声明等长文本中做宽泛
  子串匹配；
- 只有列头而没有数据行的表格标记为 `header_only_unverified`，不得作为
  generation sample；
- generation samples 中至少一份必须包含可验证业务记录；
- 缺少业务记录时不得调用 LLM，不得提供无意义的“重新生成”主操作；
- 用户应从阻断提示一步进入“导入补充样本”，自动打开单周期样本向导并携带命令
  上下文。

本次实测发现并修正：

- NX-OS `show version` 版权声明中的 `there is no warranty` 曾被误判为合法空输出；
- `show ip interface brief` 的四份表头型空输出曾被误判为结构化正样本，并浪费
  三次模型调用；
- 生成失败页曾直接展示完整 Skill JSON，现改为业务摘要，技术诊断默认折叠；
- 补样本入口曾只跳到历史任务列表，现直接打开预填命令的导入向导。

`show version` 修复后真实链路结果：

- 4 份样本全部识别为业务数据；
- ParserSpec 自动生成并采纳；
- TextFSM 使用 2 次模型调用通过本地校验和独立复核；
- 每份样本解析 1 条设备记录，主键为 `device_name`；
- 审批页展示原始输出、解析 JSON、完整模板和门禁；
- 人工确认后模板成功激活，随后进入历史样本回填阶段。

因此，最终流程不是“任何非空文本都交给 LLM”，而是“确定性判断样本是否具备
生成资格，具备资格后再让 LLM 负责语义和模板，并以最终解析结果为审批依据”。
