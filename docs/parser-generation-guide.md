# TextFSM 生成与验证

核对日期：2026-09-06。默认入口为巡检规则工作区的命令“自动制作”；人工干预时可进入命令详情查看或编辑解析方案和模板。

## 输入与结果

当前生成入口接受 Simple Parser v2 的 `job_id`，使用任务冻结的 ParserSpec 与样本。历史任务保留读取；旧的非 job 生成方式不再是支持的写入接口。

ParserSpec 说明记录边界、字段和主键。TextFSM 捕获命令中实际存在的数据；标准含义由检查项及 Adapter 确定，详见[规则架构](patrol-rule-architecture.md)。不要要求模型通过模板补造缺失的设备数据。

样本角色包括：

- `generation`：用于生成上下文和执行验证。
- `acceptance`：不作为初始生成样本，生成候选后执行验证。
- `negative`：错误提示、不支持命令或其他不应产生业务记录的输出。
- `unassigned`：尚未确定用途，需要完成准备后再推进。

模型使用脱敏证据。验证执行保存的样本；存在原始证据时还检查原始与脱敏输出的结构兼容，不要求敏感值相同。当前修复循环会将验收失败诊断、解析预览和预期信息反馈给模型，因此 `acceptance` 是另一组验证样本，不能据此宣称“模型从未接触的独立最终验收”。需要评估泛化能力时，应在制作完成后另取未参与修复的设备样本回放。

## 有界生成流程

1. 读取并冻结任务输入、样本与生成运行标识。
2. 对可复用候选重新执行验证；已有模板或确定性候选不能跳过当前输入检查。
3. 必要时调用模型提交完整候选，通过本地 TextFSM 执行获得真实记录和错误。
4. 将诊断用于定向修复，保留各轮模板、记录、错误、模型与调用统计。
5. 本地执行通过后进行独立语义复核；复核结果与硬验证分开记录。
6. 持久化候选及证据，由上层发布流程决定是否激活。

`service.py` 将生成模型调用限制为最多 3 次，Provider 重试上限为 1 次。达到上限、输入漂移或无法通过验证时停止，保留诊断供人工处理。复核服务不可用可能留下需人工复核的候选；这不是自动发布资格。

实际 TextFSM 编译与执行是解析真值；平台方言提示不应把标准 TextFSM 的合法语法直接判成执行失败。验证关注实际记录、字段、必需值、主键、类型、规范化、样本预期和负样本误匹配。多记录集合还要逐集合核对，List 值保持原生结构。

## 模型工具协议

生成器通过有限工具协议接收完整模板和返回本地执行结果，不赋予模型任意 Shell、Python 或数据库写入入口。工具调用与结果按调用 ID 关联，不能只读模型文字就认定模板成功。

当前 Provider 适配已通用化，DeepSeek 的 thinking 参数只在对应 Provider 使用。历史协议标签 `deepseek-thinking-tools-v1` 仍被持久任务引用，不能当无用字符串随意重命名。模型返回的 `reasoning_content` 随当前工具会话续传，不作为用户可见解析报告。

## 页面核对与人工干预

命令详情纵向展示 **CLI 原文 → 生成的 TextFSM → 实际解析结果 → 标准检查结果**。核对模板时以产生本次结果的版本为准，不拿另一轮模板解释当前记录。

可从历史候选开始修订 ParserSpec 或模板；修改后重新运行样本。检查项及命令关联仍由人工决定。字段转换在 Parser 实际结果上本地执行，必要时可人工修改 Projection JSON。

## 发布边界

单独调用 TextFSM 生成 Skill 的发布动作会登记候选，不等于整条巡检链已进入生产。当前默认自动制作在管理员启动或授权后，验证 Parser、标准转换、设备实现及运行包依赖，再激活可用运行包。旧手工模板审批入口仍然保留。

历史任务、已发布模板、审批摘要及回放所引用的协议不因代码清理删除。新制作流程的授权、暂停与恢复见[自动制作指南](patrol-rule-autopilot-guide-2026-09-05.md)。

## 代码与验证入口

- [生成 Skill](../src/skills/textfsm-generator/scripts/generate_templates.py)、[模型适配](../src/skills/textfsm-generator/scripts/llm_generator.py)、[独立复核](../src/skills/textfsm-generator/scripts/result_reviewer.py)
- [有界管线](../src/core/parser_generation/service.py)、[验证器](../src/core/parser_generation/validation.py)、[候选持久化](../src/core/parser_generation/publishing.py)
- [生成回归](../tests/unit/test_simple_parser_generation.py)、[复核回归](../tests/unit/test_parser_result_reviewer.py)、[生成治理回归](../tests/unit/test_parser_generation_governance.py)

运行方式及测试数据边界见[维护与验证](maintenance.md)。不以旧版本的一次模型耗时、缓存命中或模板数量作为当前验收标准。
