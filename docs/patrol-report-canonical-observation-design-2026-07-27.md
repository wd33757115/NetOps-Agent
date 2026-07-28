# 巡检报告与统一观测模型设计

日期：2026-07-27  
状态：已按本设计完成核心链路实现

## 1. 目标

参考既有巡检 Excel 的领域分表方式，建设两类互补交付物：

1. 面向甲方领导的正式巡检报告，回答当前网络是否健康、主要风险在哪里、影响范围多大以及建议如何处置。
2. 面向运维工程师的 Excel 技术附表，提供版本、CPU、内存、接口、光衰、变化、事件和证据明细。

报告不能直接依赖厂商命令或 TextFSM 字段名。所有数据必须先通过
ObservationContract 翻译为标准观测语义，再进入变化、事件和报告链路。

## 2. 参考 Excel 审计

参考文件包含 41 个工作表，主要结构如下：

| 工作表 | 数据范围 | 用途 |
| --- | ---: | --- |
| 摘要 | A1:E4266 | 按设备和命令汇总新增、缺失、修改数量 |
| 版本 | A1:R789 | 设备、板卡和子卡版本信息 |
| CPU | A1:I356 | CPU 5 秒、1 分钟和 5 分钟利用率 |
| 内存 | A1:I404 | 总量、已用量和利用率 |
| display interface brief | A1:L21200 | 接口状态明细 |
| 接口光衰 | A1:G1780 | 温度、收光和发光功率 |

值得保留的设计：

- 按运维领域分表，便于筛选、比对和交付。
- 每行保留设备、命令和原始内容，具备一定追溯能力。
- 大规模接口数据使用明细表，而不是挤入报告正文。

不能直接沿用的设计：

- `状态=不变` 只表示本期与基线相同，不能证明当前值健康。例如光衰表中
  `RX_POWER=-36.96 dBm` 仍可显示“不变”。
- 不同厂商原生字段直接成为列名，无法形成统一 CPU、内存和接口口径。
- 版本表将设备级、板卡级和子卡级记录拍平到一个宽表，产生大量空列。
- 缺少当前风险、规则覆盖率、结论可信度和阻断原因。
- 工作表样式适合技术留痕，不适合作为领导报告。

因此，现有 Excel 应作为技术附表的交互参考，而不是新的存储模型或健康判定依据。

## 3. 标准数据链路

```text
Raw CLI
  -> ParserBundle / TextFSM
  -> 厂商原生 record_sets
  -> ObservationContract
  -> 标准 StateObservation 长表
  -> ChangePolicy
  -> Change
  -> EventRule
  -> 当前风险与事件生命周期
  -> 领导报告 + Excel 技术附表
```

各层职责：

- Parser 只负责忠实提取厂商输出，不承担跨厂商统一。
- ObservationContract 负责字段语义、单位、维度和受控转换。
- StateObservation 保存标准事实，不建设跨厂商超级宽表。
- Change 表达两个时间点之间的差异。
- Event 和当前风险表达运维结论。
- 报告投影层把标准长表按领域透视成易读表格。

## 4. 跨厂商统一口径

同一指标允许不同 Parser 使用不同原生字段：

```text
H3C.cpu_5s
Cisco.cpu_total_5_sec
Huawei.cpu_usage_last_5_seconds
```

各自的 ObservationContract 都映射为：

```json
{
  "observation_type": "device.cpu.utilization",
  "unit": "percent",
  "dimensions": {
    "scope": "system",
    "window": "5s"
  }
}
```

运行时事实身份为：

```text
device + entity + observation_type + dimension_signature
```

这样可以同时保存并区分 CPU 5 秒、1 分钟和 5 分钟数据，又能在报告中稳定生成统一列。

标准语义示例：

| 领域 | 标准 observation_type |
| --- | --- |
| 软件版本 | `device.software.version` |
| 发布版本 | `device.software.release` |
| CPU 利用率 | `device.cpu.utilization` |
| 内存总量 | `device.memory.total_bytes` |
| 内存利用率 | `device.memory.utilization` |
| 接口状态 | `interface.oper_status` |
| 接收光功率 | `interface.optical.rx_power` |
| 发送光功率 | `interface.optical.tx_power` |
| 接收低告警阈值 | `interface.optical.rx_low_warning` |

单位换算和派生值只能使用平台白名单 DSL。例如设备仅输出空闲率时，可声明：

```text
device.memory.utilization = 100 - device.memory.free_ratio
```

不允许 LLM 生成任意 Python 或 JavaScript 作为运行时转换。

## 5. 多记录集命令

`display version` 等混合输出必须使用 ParserSpec v2 和 ParserBundle 拆分：

```text
device_version
slot_inventory
subslot_inventory
```

各记录集拥有独立的：

- `entity_type`
- `primary_keys`
- `fields`
- `expected_records`
- ObservationContract 映射

报告分别投影到“版本”和“板卡与子卡”，避免将设备级字段复制到每一条子卡记录。

## 6. Excel 技术附表

技术附表固定包含：

| 工作表 | 内容 |
| --- | --- |
| 总览 | 总体结论、可信度、设备数、风险数、变化数、事件数和质量覆盖率 |
| 当前风险 | 当前仍成立的异常，包含严重度、设备、实体、当前值、规则和证据 |
| 事件 | 本批次事件及创建、更新、恢复状态 |
| 变化 | 前后值、标准观测类型和策略判定 |
| 版本 | 设备型号、软件版本、发布版本、重启原因和运行时长 |
| 板卡与子卡 | 板卡、硬件、驱动、PCB、CPLD、内存和 Flash |
| CPU | 5 秒、1 分钟、5 分钟及无窗口指标 |
| 内存 | 总量、已用、可用、利用率和空闲率 |
| 接口状态 | 状态、协议、IP、速率、双工和描述 |
| 接口光衰 | 温度、收发功率及设备输出的告警阈值 |
| 其他结构化数据 | 尚未配置领域视图但已经标准化的数据 |
| 证据索引 | observation、snapshot、parser、contract、命令和采集时间 |
| 非结构化变更 | 采用 raw hash/diff 的命令及安全预览 |

每个领域表额外展示：

- 当前判定
- 本次事件
- 变化数
- 采集时间

三种概念必须严格区分：

```text
当前判定：当前值是否仍异常
本次事件：本次是否创建、更新或恢复事件
变化数：与上一有效基线相比发生多少次差异
```

没有活动规则覆盖时显示“未判定（缺少活动规则覆盖）”，不得显示“健康”。

## 7. 领导报告

Word 报告不复制全部 Excel 明细，只呈现：

1. 巡检范围与执行完成度。
2. 总体结论和结论可信度。
3. 当前风险分级及影响设备范围。
4. 已恢复事项和新增变化。
5. CPU、内存、接口、光衰等领域摘要。
6. 资产覆盖缺口及其对结论的影响。
7. 整改建议、优先级和证据编号。

正文结论必须来自规则评估后的当前风险，不能用“无变化”替代“健康”。

## 8. 光衰阈值

光模块阈值不按设备型号或厂商硬编码。优先级为：

1. 同一接口、同一光模块在本次输出中携带的 warning/alarm 阈值。
2. 经审批的模块规格资产。
3. 明确标记为兜底的组织策略。
4. 均不存在时返回 `threshold_unknown`，不自动判为健康或异常。

阈值作为观测上下文随接口事实进入 ChangePolicy 和 EventRule。不同接口可以使用不同阈值。

## 9. 大规模交付

领域明细采用冻结首行、筛选器和稳定列宽，支持工程师处理万级行数。报告正文只呈现聚合结果。

当前实现按请求即时生成批次 Excel，适合功能验收和中等批次。生产环境达到 1000 至 2000 台设备后，应进一步将报表生成改为异步任务：

```text
请求导出
  -> 冻结批次证据清单
  -> 后台生成
  -> 对象存储
  -> 带权限和有效期的下载地址
```

这样可避免 Web 请求超时，并保证同一批次多次下载内容一致。

## 10. 已实现代码

- `src/core/observation/semantic_projection.py`
  - 标准语义、维度、单位和安全转换。
- `src/core/parser/parser_bundle.py`
  - 多 record_sets ParserBundle。
- `src/core/state/normalizer.py`
  - 标准事实和稳定维度身份。
- `src/core/patrol/change_policy.py`
  - 同一 observation_type 的维度化策略分派。
- `src/core/patrol/event_rules.py`
  - 维度化规则匹配、逻辑身份和事件隔离。
- `src/core/patrol/observation_report.py`
  - 标准事实到领域表的投影。
- `src/core/patrol/reporting.py`
  - 批次 Excel 技术附表。
- `src/gateway/workflow_api.py`
  - 批次技术附表下载接口。

前端在巡检批次交付物中提供“技术附表”下载，原始输出、事件数据、正式报告和技术附表保持独立。

## 11. 验收标准

- 同一 CPU 指标在不同厂商和命令下进入相同标准列。
- 5 秒、1 分钟和 5 分钟指标不会互相覆盖或误触发规则。
- 设备、板卡和子卡记录不会被拍平成同一稀疏宽表。
- 光衰表同时展示当前值、设备阈值、当前判定、事件和证据。
- “无变化”不会被解释为“当前健康”。
- 缺 Parser、Contract、Policy 或 EventRule 时，报告明确降低可信度并说明缺口。
- 任一结果可追溯到 Snapshot、Parser、Contract、ChangePolicy 和 EventRule。
