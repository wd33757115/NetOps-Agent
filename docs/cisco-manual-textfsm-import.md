# Cisco 脱敏样本与手工 TextFSM 导入

核对日期：2026-09-23。此批次使用用户提供的本地“思科”目录，参考本机
ntc-templates 9.1.0，直接编写并验证模板。没有调用规则治理的模板生成、
语义建议或模型服务。

2026-09-24 后续状态：已按当前检查项重建变化策略与事件规则，并修正本批精确型号筛选及
Parser 兼容性记录。70 条检查项适配路径的运行包已启用；原始数据完整性要求未放宽。
详见[规则重建记录](check-item-semantic-rule-refresh.md)，下文未启用运行包的说明属于导入阶段记录。

## 已落库内容

| 内容 | 结果 |
| --- | --- |
| 源文件 | 7 个脱敏文件，保存到 admin 的私有文件空间 |
| 导入任务 | `offline-728651524cda433080e7`，已完成 |
| 有效命令样本 | 105 段：91 段结构化解析、14 段配置原文 |
| 排除项 | 33 段：7 段终端设置、26 段空输出／只有表头／不支持的命令 |
| 手工模板源码 | 39 个记录集模板，按型号和命令组成 50 个已启用 Parser 资产 |
| 命令适配器 | 58 个已发布关联，包括 8 个配置文本适配器 |
| 检查项 | 新增 8 项；内存进程改用 PID 与进程名共同标识 |
| 自动生成任务 | 0 |

任务名称为“TEST Cisco 手工 TextFSM 样本（采集时间未核实）”。导入时间仅用于
组织样本，不代表实时采集时间；本任务没有建立当前巡检健康结论。

设备型号来自 `show version`：N9K-C9364C、N9K-C93180YC-FX、
ISR4451-X/K9、WS-C2960XR-24TS-I。未将文件名简称直接当作型号。

## 用户指定的处理边界

- 主机名前缀统一替换为 `TEST`，指定私网地址前缀替换为 `192.168`。
  新样本、文件名、Parser 内容与验证元数据均通过残留检查。原始桌面文件未修改。
- 命令标题决定样本归属。`running-config` 中的 vPC 内容保留在配置原文中，
  即使出现命令提示符，也不再拆成独立运行状态样本。
- 只有表头或无输出的命令不参与本次模板生成和验证；两个 VRRP 空表没有生成资产。
- 配置采用现有文本比较方式，不强行转换为 TextFSM 记录。
- 删除本批范围内的 3 个旧 Parser、18 个旧制作任务、8 个旧命令适配器及关联。
  同时移除对应启动种子，避免重启后重新创建旧关联；删除审计只记录 ID 和数量。
- “接口状态运行信息和统计信息”的既有 Value 定义没有修改。

## 新增检查项

| 检查项 | 数据边界 |
| --- | --- |
| `system.cpu_instant` | 用户态、内核态与空闲 CPU 瞬时百分比 |
| `system.memory_pool` | 按内存池记录字节容量、使用量与空闲量 |
| `redundancy.stack_ports` | 堆叠端口状态与对端成员 |
| `interface.switching_statistics` | 按接口、交换路径记录包和字节计数 |
| `interface.controller_statistics` | 控制器状态、帧长、速率及驱动计数 |
| `system.debug_status` | 实际显示的调试状态证据 |
| `interface.optical_measurements` | IOS 分表输出，按接口和指标记录读数、门限、单位 |
| `environment.sensor_reading` | 传感器位置、状态、读数与原文单位 |

新增项只提供证据，没有自动添加故障判定或启用运行包。NX-OS 瞬时 CPU
没有冒充五分钟均值；内存字节没有填进 KB 字段；SysPwr/PoEPwr 没有映射成
交流输入／电源输出状态。光模块保持端口和逐通道记录，不按行号拼接不同表。

## 解析成功与业务完整性

115 次记录集回放保留 5,477 条记录，40 项独立计数／唯一性检查通过。
实际 ParserRuntime 对 91 段结构化样本全部解析成功；14 段配置原文的摘要
与准备阶段逐条一致。导入后的 105 条快照也通过全文摘要一致性检查。

这不代表全部检查项已具备健康判定数据。按现有严格定义执行 112 组适配器输出，
59 组完整、2 组部分完整、51 组不可用。缺失来源包括 BOOT_IMAGE、SLOT、
component_type、VRF、地址族及光功率／通道；另有一个有效 vPC 域没有成员行。
原始解析记录仍保留，缺失值不补零、不推断成正常。后续应补充采集命令或明确
业务语义，再验证健康规则；不能把这些数据缺口算作模板解析失败或设备故障。

内存进程中的多个 PID 0 是不同内部进程，因此模板与检查项均按
`PROCESS_ID + PROCESS` 保持唯一，回放不再丢弃这些记录。

## 维护与复验

模板与 NTC 来源清单位于 `scripts/check_items/cisco_manual_templates/`，
包含许可证及新增检查项定义。可在仓库根目录进行纯本地复验：

```powershell
venv/Scripts/python.exe scripts/check_items/cisco_manual_batch.py <样本目录> --output .runtime/cisco-replay
```

该脚本只生成脱敏文件与验证报告，不写数据库、不发起模型任务。
本次落库审计及完整性结果位于 `.runtime/cisco-manual-import/final-verification.json`，
业务完整性详情位于同目录的 `projection-quality.json`。

相关回归覆盖命令边界、空输出排除、模板编译、字段维度、已批准模板的运行时选取、
目录治理、检查项语义与防止旧关联重新初始化。扩大测试时，两条旧的默认事件生成
用例仍失败（`test_event_builder_distinguishes_change_from_event`、
`test_event_builder_handles_initial_abnormal_state`），未计入本批通过数，未据此宣称全量回归通过。

重载时发现 Windows PowerShell 5 会把 Celery 正常 stderr 日志提升为终止错误。
`scripts/test/celery_beat.ps1` 现在在严格完成 PID 检查后，通过原生进程退出码判断
启动是否成功；已有 PID 恢复与重复进程保护仍保留。
