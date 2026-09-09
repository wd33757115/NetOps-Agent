# CPU、内存、风扇、电源字段对齐

2026-09-09，按用户授权修订现有四个检查项及其 H3C Comware7 命令解析资产。

## 数据约定

检查项标准字段与 TextFSM Value 名称完全相同。适配器只保留同名读取，不再做字段改名、状态文字转换、容量单位换算或使用率计算。命令与检查项关联保持人工已有配置。

| 检查项 | 身份字段 | 主要数据字段 |
| --- | --- | --- |
| CPU 使用率 | slot、cpu | cpu_5s_percent、cpu_1m_percent、cpu_usage_percent（五分钟），可选设备阈值 |
| 内存使用率 | slot | memory_total_kb、memory_used_kb、memory_free_kb、memory_shared_kb、memory_buffers_kb、memory_cached_kb、memory_free_percent；可选直接采集的五分钟使用率及设备阈值 |
| 风扇状态 | slot、fan | fan_status、fan_airflow_direction、preferred_fan_airflow_direction |
| 电源状态 | slot、power_supply | power_supply_status、mode、current_amperes、voltage_volts、output_watts、fan_direction、slot_input_watts |

`slot_input_watts` 是槽位总输入功率，在槽位的各电源行重复展示，不可按电源行求和。`--` 数值保留为空，不填零。

CPU 字段的固定测量窗口写入检查项的 `fixed_dimensions` 语义元数据，生成状态观察时使用，不伪装成 CLI 字段。定义校验拒绝非法窗口以及固定、动态维度重叠。规则生成上下文也携带固定维度。

CPU、内存沿用设备阈值和方向性趋势健康策略。现有内存 CLI 只有瞬时容量与 FreeRatio，没有五分钟使用率，不能用容量比例冒充五分钟测量值。设备未输出的阈值留空，健康判定不得因此视为正常。风扇、电源保留 `Normal`、`Faulty` 等原文，判定时忽略大小写，修复旧规则遗漏 `Faulty` 的问题。

## 发布与验证

`scripts/test/revise_hardware_values.py --source-job <已有双周期离线任务>` 默认只读，验证两个周期的真实 CLI、原有记录数量、主键唯一性及同名适配器结果。加 `--apply` 后，在同一数据库事务中发布四个检查项、四个 Parser、四个适配器，并将已有风扇、电源状态规则重新绑定到新检查项版本，记录操作审计。

旧资产退出当前运行，不再参与新任务选择；已完成任务引用的不可变证据不改写。已删除旧 CPU scope 补丁及对应旧测试。内存历史策略修订入口不再给同名适配器补写瞬时窗口。

离线任务“巡检数据 Value 对齐验证”使用原有上传文件重新执行，无外部 AI 调用。两周期每台设备分别得到 CPU 1 条、内存 1 条、风扇 5 条、电源 2 条记录。已验证独立 Excel、原 Excel 下载入口和 Word 证据包内 Excel，检查变化标红及原有光功率恢复结果。四个工作表的列均为检查项标准 Value，结构化表不混入 CLI 原文行。

当前真实模板验证范围是本地 H3C Comware7 / S6850-56HF 样本。其他厂商后续生成模板同样受同名契约约束，不能把未验证的模板标为可用。
