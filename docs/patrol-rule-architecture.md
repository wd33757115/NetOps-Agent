# 巡检检查项与规则架构

核对日期：2026-09-06。本文描述当前执行路径；操作步骤见[规则自动制作](patrol-rule-autopilot-guide-2026-09-05.md)。

## 人工定义与自动实现

人工决定检查什么、如何判断，以及一条设备命令属于哪个检查项。检查项版本固定标准语义、输出契约和判断要求。AI 负责解析方案、TextFSM 与必要的字段转换；变化和事件规则根据检查项标准语义确定性编译。管理员启动或授权后，自动流程通过完整验证再发布。

```text
人工 CheckItemVersion ───────────────→ 标准契约 B、判断与规则要求
人工命令关联 + 脱敏 CLI 样本
  → ParserSpec → TextFSM → 厂商记录 A
  → CommandAdapter v2 Projection → 标准记录 B
  → 检查项评估 / 状态 / 变化 / 事件
  → 冻结报告与证据
```

业务用户从工作台的“网络巡检”发起在线或离线巡检。配置人员从“巡检规则生成”维护检查标准、命令实现与制作任务。设备管理和规则配置使用 rule-governance App 使用权，详见[权限指南](permissions-guide.md)。

## 各层只负责一件事

| 对象 | 职责 | 不应承担的职责 |
| --- | --- | --- |
| CheckItemVersion | 定义稳定业务身份、标准字段、单位、主键及检查含义 | 根据某台设备临时输出改写业务标准 |
| ParserSpec / TextFSM | 划分记录、捕获厂商字段，忠实解析 CLI | 凭字段名称猜业务结论 |
| CommandAdapter v2 | 将一个来源 record set 转为检查项标准记录，显式处理类型、单位、枚举与维度 | 通过任意脚本执行隐藏业务逻辑 |
| ChangePolicy / EventRule | 按标准语义比较与生成事件 | 使用原始 CLI 或厂商字段重新推断检查目标 |
| RuntimeBundle | 固定设备范围和可用资产版本，校验完整依赖 | 把“某个模板已发布”等同于整项巡检可用 |
| ReportManifest | 固定本次执行事实、资格、版本和证据 | 读取最新资产重写历史结果 |

字段命名优先采用检查项标准词汇，但“同名”不代表类型、单位、空值和枚举相同。仍在使用的按范围 Value 映射与 Projection 不能仅因名称统一而删除。标准字段同名且语义一致时可以直接映射；不一致时由显式转换处理。

规则生成输入只接受检查项标准语义。`canonical_rule_context.py` 明确拒绝 Parser 模板、原始 CLI、样本记录及厂商字段等采集上下文，避免不同厂商生成互相冲突的业务规则。

## 执行与质量

### 在线采集命令与检查标准分开配置

设备管理中的“巡检命令集”保存有序 CLI 命令清单，通过设备 ID、设备分组、动态分组或筛选条件绑定设备范围。在线采集按该清单执行；检查项与命令的人工关联用于后续解析和判断，不反向扩大或替换清单。命令只去除首尾空白和完全重复项，保留参数大小写与内部空格。

绑定前预览所有匹配设备，列表可以分页或截断展示，但保存的覆盖证据必须评估全部匹配设备。无匹配设备不能启用。相同范围、相同采集用途重新绑定时停用原绑定并记录审计；备份等其他用途不受影响。不同范围重叠时，优先级数字小者优先，同优先级按创建顺序生效，不合并清单。显式巡检绑定优先于设备历史 `command_file`。

明确绑定的命令清单在缺少 Parser 时仍采集并保留 CLI 原文，同时记录解析缺口，不能因此宣称检查完整。连接与执行环境的门禁仍生效。采集完成后，仅对实际采集的命令附加已发布且无歧义的检查项分析关联；已有冻结关联不会被覆盖。此行为不改变其他巡检模式的缺失 Parser 策略。

既有检查项型 Profile 保留兼容，不自动迁移。新设备配置界面只创建、复制和绑定命令清单。新建或复制使用独立名称，避免激活同名新版本时连带影响其他范围；检查项仍在规则工作台维护。

`command-adapter/v2` 将实际 Parser 记录转换成 `ObservationBatch`，携带设备、检查项、观测时间、Parser/Adapter 证据、检查项版本及质量状态。运行时直接消费标准记录。

质量状态包括 `complete`、`valid_empty`、`partial`、`unavailable` 和 `not_applicable`。空输出、采集失败与健康结果不是同一个状态；应保留诊断与证据，不能补造“正常”记录。当前自动发布流程对正样本逐份运行生产 Parser 和 Adapter，负样本不得产生业务数据。

并非每个检查项都需要完整的变化和事件链。资产需求来自检查项版本，运行包按实际输出选择规则。文本检查项采用显式 TextComparisonProfile，不强制生成 TextFSM，也不把结构化解析失败降级成文本检查成功。

## 发布、接管与兼容

默认流程是 `command_autopilot.py`，详见[自动制作指南](patrol-rule-autopilot-guide-2026-09-05.md)。人工可暂停、修改解析方案或 Projection，再继续验证。输入摘要、检查项版本或作用域变化后必须重新检查下游结果及发布授权。发布记录保留授权人和任务身份。

旧 ObservationContract 路径及 Adapter v1 仍用于兼容已有资产和历史回放。它们不是新检查项必须逐一手工配置的默认层级。旧候选协调器与手工审批 Workflow 的边界见[资产治理指南](rule-asset-control-plane-guide.md)。历史读取代码须结合持久化数据验证后退出，不能按名称批量删除。

## 代码与验证入口

- [标准转换](../src/core/check_items/command_adapter.py)、[规则上下文](../src/core/check_items/canonical_rule_context.py)
- [运行包装配](../src/core/check_items/runtime_bundle_assembly.py)、[自动发布](../src/core/rule_control/command_autopilot_release.py)
- [Adapter 回归](../tests/unit/test_command_adapter_v2.py)、[规则上下文回归](../tests/unit/test_canonical_rule_context.py)、[自动发布集成回归](../tests/unit/test_command_autopilot_integration.py)

测试样本证明对应分支的行为；目标设备和模型质量仍应使用该环境的脱敏样本验证。通用回归命令见[维护与验证](maintenance.md)。
