# 巡检检查项与命令适配器简化改造方案（2026-08-10）

## 1. 决策结论

本项目不再继续扩展“每条命令自动生成 ObservationContract、ChangePolicy、EventRule 完整资产包”的复杂链路。

改造后只保留两个面向用户的业务对象：

1. **巡检检查项（Check Item）**：定义检查什么、标准结果是什么、如何判断变化和风险。
2. **命令适配器（Command Adapter）**：定义某类设备执行什么命令，以及如何把命令输出转换为检查项标准结果。

现有 `ObservationContract`、`ChangePolicy`、`EventRule` 数据表与运行时解析器在迁移期继续保留，但降级为内部编译产物，不再要求用户逐层生成、逐层审批。

最终业务链路为：

```text
设备 + 检查项计划
→ 匹配命令适配器
→ 采集原始 CLI
→ TextFSM 解析与字段转换
→ 标准 ObservationBatch
→ Change / 当前值判定
→ Event
→ 事实、风险、证据与巡检报告
```

## 2. 产品目标

面向多厂商、多型号和大规模网络设备，把在线或离线巡检数据转换成真实、可解释、可追溯的网络现状、变化和风险，最终交付：

- 甲方领导看得懂的结论；
- 工程师能够复核的标准表格；
- 每项结论可回到设备、命令、原始输出和规则版本的证据链；
- 数据不足时明确说明“无法判断”，不包装成“未发现异常”。

Parser、LLM 和规则控制面是生产资料，不是产品本身。可信的巡检结果和报告才是最终产品。

## 3. 不再采用的设计

以下做法退出主链路：

- 以 `vendor + model + command` 作为最高层业务对象；
- 每条命令分别生成 ObservationContract、ChangePolicy 和 EventRule；
- 自动生成的 ParserSpec 或推断记录数反向否决实际正确的解析结果；
- 使用多套 Provider、目标分区和语义投影算法竞争决定业务语义；
- 一个下游目标缺失就让整条命令和其他正确检查项一起失败；
- 让生产巡检临时调用 LLM 判断状态或阈值；
- 要求每条 CLI 命令都结构化；
- 隐式 fallback 命令；未匹配采集实现时只记录明确的覆盖缺口。

## 4. 最小业务模型

### 4.1 巡检检查项

检查项定义跨厂商一致的业务结果。

```yaml
id: interface.oper_status
name: 接口运行状态
mode: structured

fields:
  interface: {type: interface_name, required: true}
  oper_status: {type: enum, values: [up, down, admin_down, unknown], required: true}
  description: {type: text, required: false}

primary_keys: [interface]

change:
  tracked_fields: [oper_status, description]

evaluation:
  - id: interface_down
    when: current.oper_status == "down"

events:
  - id: InterfaceDown
    when: previous.oper_status == "up" and current.oper_status == "down"
  - id: InterfaceUp
    when: alert_open("InterfaceDown") and current.oper_status == "up"
```

一个检查项只维护一套标准字段和判断规则。不同厂商的命令适配器共同产出这套结构。

非结构化检查项使用 `mode: text_diff`，只定义文本去噪和差异分类，不需要 TextFSM。

### 4.2 命令适配器

命令适配器定义设备适用范围、采集命令、Parser 和标准结果转换。

```yaml
id: cisco.nxos.show_system_resources

match:
  vendor: Cisco
  family: NX-OS
  model_regex: "N9K.*"
  os_version_regex: null

command: show system resources
mode: structured

parser:
  template_id: parser-template-system-resources

outputs:
  - check_item: system.cpu_usage
    source_record_set: cpu_cores
    fields:
      scope: literal("cpu")
      scope_id: cpu_id
      cpu_usage_percent: 100 - cpu_idle_percent

  - check_item: system.memory_usage
    source_record_set: system_resources
    fields:
      scope: literal("device")
      scope_id: device_id
      memory_usage_percent: 100 * memory_used_kb / memory_total_kb
```

一个命令只引用一个不可变 Parser 资产。该资产可以是含多个命名记录集的 `ParserBundle`，一个命令因此仍可产生多个检查项；每个输出必须指定自己的 `source_record_set`。未完成的输出可以从候选中移除，不影响同一命令的其他正确输出发布。

第一版不支持跨命令拼接一个检查项。单个适配器输出必须独立形成完整标准记录，避免引入隐式 join 和时间对齐复杂度。

## 5. TextFSM 字段命名

### 5.1 原则

- 语义和单位完全相同的字段必须使用相同 `Value` 名称；
- 语义相同但单位不同的字段保留单位后缀，例如 `memory_total_kb`；
- 语义不同的字段不得为了统一而错误合并，例如 `cpu_idle_percent` 不能冒充 `cpu_usage_percent`；
- 模糊名称如 `status`、`value`、`usage` 不允许直接进入新发布模板；
- 最终 Excel 和报告只读取检查项标准字段，不直接依赖厂商模板字段。

### 5.2 字段词汇来源

字段词汇由检查项的标准字段和已审批的源测量字段组成。LLM 生成时必须先收到该词汇子集：

```text
interface
oper_status
admin_status
cpu_usage_percent
cpu_idle_percent
memory_total_kb
memory_used_kb
rx_power_dbm
rx_low_warning_dbm
```

LLM 找不到精确语义时可以提出新字段，但必须在人工确认页标记为“新增字段建议”。本地代码不根据名称猜测业务含义。

## 6. 唯一资产生成流程

### 6.1 结构化命令

```text
导入离线样本
→ 选择或新建检查项
→ LLM 生成 TextFSM、源字段和标准字段转换
→ Python TextFSM 执行
→ 执行转换并形成标准表格
→ 把完整表格或执行错误返回 LLM
→ 最多修正三轮
→ 人工确认
→ 发布命令适配器
```

LLM 每轮必须看到：

- 脱敏原始 CLI；
- 当前 TextFSM；
- TextFSM 实际解析结果或异常；
- 标准字段转换结果；
- 上一轮人工修改内容。

人工确认页固定展示：

1. 原始 CLI；
2. TextFSM 模板；
3. Parser 源结果表；
4. 检查项标准结果表；
5. 主键、类型、单位与空输出语义；
6. 若有双周期样本，展示 Change、判定和 Event 回放。

工程师可以在任何阶段返回上一步，修改模板、字段、主键、转换或检查项绑定。工程师也可以跳过 LLM 初审直接提交人工确认。

### 6.2 文本差异命令

```text
原始 CLI
→ 去除时间、分页符和已审批动态噪声
→ 保存规范化文本与哈希
→ 双周期行级或块级 Diff
→ LLM 仅归纳已经存在的差异
```

配置、ACL、策略和许可证等命令可以选择 `text_diff`，不因为缺少 TextFSM 被判为巡检失败。

### 6.3 判断规则生成

只有新建检查项或检查项没有已发布规则时才生成判断规则：

```text
检查项标准结构 + 双周期标准数据
→ LLM 生成 Change、当前值判定和 Event 候选
→ 本地执行历史回放
→ 将回放结果返回 LLM，最多三轮
→ 人工确认
→ 发布检查项新版本
```

新增厂商或型号的命令适配器直接复用检查项规则，不重复生成规则。

## 7. 本地验证边界

本地代码只执行客观检查：

- TextFSM 能编译；
- 对样本执行时不抛异常；
- 字段转换表达式可执行且结果类型正确；
- 主键非空且在同一完整快照内唯一；
- 发布内容与人工审批内容摘要一致；
- LLM 输入不包含禁止发送的敏感内容。

本地代码不再推断：

- 应有多少记录；
- 哪些字段具有运维价值；
- 某个厂商字段应该属于哪个检查项；
- 某种状态必然代表告警。

这些语义由 LLM 提议、人工确认。

## 8. ObservationBatch 与数据质量

命令适配器每次执行产生一个运行结果信封，它不是新的治理资产：

```json
{
  "device_id": "device-001",
  "check_item_id": "interface.oper_status",
  "observed_at": "2026-08-10T10:00:00Z",
  "quality_status": "complete",
  "adapter_version": 3,
  "check_item_version": 2,
  "records": [],
  "evidence": {
    "snapshot_id": "snapshot-001",
    "parser_template_id": "template-001"
  }
}
```

质量状态固定为：

| 状态 | 含义 | Change/Event 行为 |
|---|---|---|
| `complete` | 完整可信 | 可完整比较和判断 |
| `valid_empty` | 命令成功且已确认确实为空 | 可按检查项配置判断实体移除 |
| `partial` | 结果可能不完整 | 只比较已有实体，不推断移除 |
| `unavailable` | 采集或解析失败 | 不生成业务 Change/Event，生成数据质量问题 |
| `not_applicable` | 设备不支持 | 不参与检查，报告说明范围 |

没有解析到记录不得自动解释为实体删除或健康。

## 9. Change、阈值与 Event

### 9.1 Change

前后周期使用以下键关联：

```text
device_id + check_item_id + primary_keys
```

只比较检查项声明的 `tracked_fields`。数值指标可以配置 `change: none` 或显著变化容差，避免每次 CPU 数值波动都生成 Change。

主键规范化必须保存 `source_key`、`entity_key` 和规范化版本，防止 `Eth1/1` 与 `Ethernet1/1` 产生虚假的新增和删除。

### 9.2 版本可比性

前后周期必须使用相同的命令适配器版本和检查项版本。版本不同时优先使用当前适配器重新解析基线原始 CLI；无法重放时返回 `not_comparable`，不生成伪 Change。

### 9.3 阈值判定

阈值解析顺序固定为：

```text
设备输出阈值 → 管理员 Profile 覆盖 → 检查项默认值 → unknown
```

每次判定保存阈值数值、单位和来源。单位不一致、阈值无效或没有阈值时输出 `unknown`，不能输出“正常”。

阈值判定直接使用当前 Observation，即使没有历史基线，首次巡检也能发现当前超限。

### 9.4 Event 状态

Event 幂等键为：

```text
device_id + check_item_id + entity_key + rule_id
```

状态转换固定为：

```text
normal  → warning/critical：创建事件
warning → warning：更新持续时间，不重复创建
warning → normal：创建恢复记录并关闭事件
unknown：保持已有状态，不错误恢复
```

晚到的旧 Observation 不得覆盖较新的事件状态。

## 10. 控制面设计

控制面只提供三个主视图：

1. **检查项目录**：维护标准字段、主键和判断规则。
2. **命令适配器**：维护设备范围、命令、Parser 和标准结果转换。
3. **样本任务**：导入离线文件并生产或修复适配器。

任务状态只保留：

```text
信息收集中 → 生成中 → 待确认 → 已发布
                       ↘ 失败
```

详情页展示步骤和历史结果，但不再把 Contract、CurrentState、Policy、EventRule 分别表现为用户必须理解和操作的并列生产线。

## 11. 迁移计划

### Phase 0：冻结旧链路扩张

- 不再给命令级 Contract 投影、目标分区和本地语义门禁增加新特例；
- 禁止新功能直接依赖 `rule-asset-generation` 的中间步骤结构；
- 建立本方案的架构测试，防止继续增加第三种业务资产。

验收：新需求能够归入检查项或命令适配器，不能归入时必须重新审视需求边界。

### Phase 1：建立两个简单对象

- 建立可版本化的 `CheckItem` 和 `CommandAdapter` 服务接口；
- 复用现有 ParserTemplate 存储，不复制模板正文；
- 将字段转换、`source_record_set`、空输出语义收进 CommandAdapter；
- 从 CheckItem 编译现有 ObservationContract、ChangePolicy 和 EventRule 运行格式。

验收：`show system resources` 可以用一个适配器独立发布 CPU 和内存输出，不再因一个目标缺失导致整条命令失败。

### Phase 2：替换生成流程与 UI

- 新生成入口只接受样本任务 ID 和命令适配器候选 ID；
- 实现最多三轮的“LLM 生成 → 本地执行 → 结果反馈”循环；
- 将审批页收敛为原文、模板、源表、标准表和回放结果；
- 新增检查项和适配器均可在 UI 创建、编辑、停用和软删除。

验收：用户不需要理解 ObservationContract 才能完成一条规则资产生产。

### Phase 3：切换运行时

- 在线和离线巡检统一通过 CheckItem 解析 CommandAdapter；
- 生成 ObservationBatch 并执行版本可比性、Change、判定和 Event；
- 对现有 active Parser/Contract/Policy/Rule 生成只读兼容适配器；
- 按检查项逐批切换，不进行长期双写。

验收：相同检查项的不同厂商输出形成相同标准列，并复用同一判断规则。

### Phase 4：删除旧生成代码

只有在调用图、API 流量和测试都证明新入口已经接管后，删除：

- `src/workflows/network/rule-asset-generation/`；
- `src/skills/observation-contract-generator/`；
- `src/skills/rule-target-planner/`；
- 命令级 Contract 目标分区与投影推断代码；
- 无运行入口的 Provider Registry 和 Contract revision loop；
- 仅服务旧工作区的前端状态、抽屉和 API 类型；
- 只测试被删除实现的测试文件。

以下能力不得随旧生成代码删除：

- ParserTemplate 版本、审批、回滚和运行时解析；
- CurrentState、Change、Event 存储；
- Change/Event 确定性执行引擎；
- 原始 CLI 与证据链；
- 历史回放与巡检报告；
- 手工内置规则 Provider/Registry 兼容接口。

## 12. 代码与文档清理规则

删除代码必须同时满足：

1. 不在生产入口、Celery 路由、Workflow 或 API 中引用；
2. 没有活动资产要求该实现才能读取；
3. 对应测试已由新链路测试替代；
4. `rg` 调用审计、单元测试和前端构建通过。

历史设计文档不再混入正式文档索引。被本方案替代但仍有审计价值的文档移入 `docs/archive/`；重复、损坏或仅描述已删除代码的文档直接删除。

## 13. 测试计划

- Cisco/H3C/Huawei 同一检查项输出相同标准列；
- 一条命令多个 `source_record_set` 不交叉污染；
- TextFSM 字段同义同单位时使用统一名称；
- CPU idle 能正确转换为 CPU usage；
- 光功率使用设备自带阈值并保留阈值来源；
- Parser 失败不会被解释成接口删除或恢复；
- `partial` 不产生实体删除；
- 适配器版本变化后先重放基线；
- 首次巡检超阈值能产生当前风险；
- 持续超限不重复创建事件；
- 恢复产生恢复记录；
- 文本检查项不要求 TextFSM；
- LLM 三轮失败后进入人工处理，不继续无限重试；
- 人工修改后的模板、转换和审批摘要保持一致；
- 两个目标型号的所有命令均被归类为结构化适配器、文本差异或不适用。

## 14. 完成标准

- 用户只需要理解“检查项、命令适配器、样本任务”；
- 新增厂商型号只新增适配器，不重复生成检查项规则；
- 一条命令的部分输出失败不会拖垮其他正确输出；
- Change 不会由采集失败、解析失败或版本变化伪造；
- 阈值无来源时明确返回 `unknown`；
- 最终报告可以逐项回答覆盖范围、当前状态、变化、风险和证据；
- 旧命令级规则生成 Workflow、无调用方模块和对应 UI 已删除；
- 代码库不存在同时可写的两套规则资产生产主链路。

一句话定义最终架构：

> 检查项定义需要什么标准结果以及如何判断，命令适配器负责不同设备怎样产生这个结果；LLM 负责候选，人工负责最终语义，生产运行时只执行已发布的确定性资产。

## 15. 实施状态与删除边界

截至 2026-08-10，本轮已经完成：

- `CommandAdapter` 最小数据契约、数据库版本和确定性字段映射执行器；
- 一条命令绑定一个 Parser 资产，并可从多个 record set 输出多个检查项；
- `ObservationBatch` 的 `complete / valid_empty / partial / unavailable / not_applicable` 质量语义；
- State Normalizer 对适配器结果的接入，以及不完整结果禁止推断实体删除；
- 检查项与命令适配器的创建、编辑、版本审批和预览接口；
- Parser 控制面收敛为“检查项目录、命令适配器、样本任务”三个生产入口；
- 删除已无引用的旧前端生产单元、判断策略和自动化控制组件及对应 API 客户端代码；
- 删除采集实现中的 fallback 命令，未匹配时只产生明确覆盖缺口；
- Parser 本地校验只保留编译、执行和证据一致性等客观检查，语义完整性由 LLM 初审和人工终审。

迁移期仍保留、暂不删除：

- `ObservationContract`、`ChangePolicy`、`EventRule` 表、Repository 和确定性运行引擎；
- `RuntimeBundle` 装配、激活、回滚和现有生产资产读取；
- 当前仍被 Celery、Workflow、API 或已发布资产引用的 `rule-asset-generation` 兼容链路。

这些保留项不是新的用户操作模型。Phase 3 完成检查项到内部运行资产的自动编译和切换后，才能按 Phase 4 的调用审计删除旧生成 Workflow。不得为了减少文件数量提前删除生产运行依赖。

当前验证样本任务仍暴露真实资产缺口：两个周期各有 14 条结构化命令缺少可用 Parser，部分检查项尚无完整判断规则。因此控制面简化已经落地，但“两个型号全部命令可形成最终巡检报告”尚未达成；这些缺口必须继续通过样本任务逐项生成、确认和回放，不能用空结果包装成完成。
