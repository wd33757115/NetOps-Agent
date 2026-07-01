# Rule Asset Generation Workflow

这个 Workflow 用于离线生成和验证规则资产候选：

```text
Structured Records
  -> ObservationContract
  -> ChangePolicy
  -> EventRule
  -> Deterministic Validation
```

它不会直接激活生产规则，也不会读取设备密码或连接网络设备。生产链路只执行已经审批激活的模板、契约、策略和事件规则。

## 输入

- `command`: canonical command。
- `records`: TextFSM 解析后的结构化记录，必须是脱敏样本。
- `observation_type`: 标准观察类型。
- `field_name`: 要生成变化策略的字段。
- `field_semantics`: 字段语义，例如 `gauge/percent`。
- `historical_values`: 脱敏历史值，用于阈值和 deadband 建议。

## 输出

- ObservationContract 候选与验证报告。
- ChangePolicy 候选与统计摘要。
- EventRule 候选与确定性验证报告。
