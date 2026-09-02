# H3C 离线样本 Parser 与 Adapter Projection 操作手册

## 1. 目的与范围

本手册用于以离线 CLI 证据建立并验收 H3C 的结构化采集资产。系统现在分成两条独立链路：

```text
采集链：离线样本 -> TextFSM Parser -> Parser record set -> Adapter V2 projection
                 -> 标准 Observation

规则链：CheckItem 标准语义 B -> CanonicalRuleContext -> ChangePolicy -> EventRule

运行时：标准 Observation + 已发布 ChangePolicy/EventRule -> Change / Event
```

本次使用的样本根目录为 `C:\Users\wangd\Desktop\TEXTFSMTEST`，选取两条命令：

| 命令 | 目标 CheckItem | Parser record set | 关键标准字段 |
| --- | --- | --- | --- |
| `display interface brief` | `interface.oper_status` | `interfaces` | `interface`、`oper_status` |
| `display transceiver diagnosis interface` | `interface.optical_power` | `optics` | `interface`、`channel`、`transceiver_present`、`rx_power_dbm`、`tx_power_dbm` |

这是预发布/测试环境的流程，不授权生产发布或全量切换。Parser 候选通过回放后仍必须经过人工语义复核、独立发布审批，以及全部活动结构化检查项的完整回放门槛。

## 2. 前置条件

1. PostgreSQL、Redis、RabbitMQ、MinIO、Qdrant 可用，Docker Desktop 状态为 Ready。
2. 项目依赖已安装在项目虚拟环境 `venv` 中。
3. 离线样本目录结构保持如下形式：

   ```text
   C:\Users\wangd\Desktop\TEXTFSMTEST
   ├─ Q1\SW1-H3CS6000-192.168.1.1.txt
   └─ Q2\SW1-H3CS6000-192.168.1.1.txt
   ```

4. 启动测试环境：

   ```powershell
   .\scripts\test\start.ps1 -SkipMiddleware -SkipLangfuse -AllowNoDocker
   ```

   启动完成后，前端为 `http://localhost:3000`，FastAPI 为 `http://localhost:8000`。

5. 使用测试环境中已配置的管理员账号登录。不要在离线样本任务中填写生产设备密码或未脱敏的外部密钥。

如果 Docker Desktop 因 Docker AI/Inference 初始化错误而无法启动，可在 Docker Desktop 设置中关闭 Docker AI 后重启；此项只是本机开发环境兼容措施，不影响项目业务数据。

## 3. 样本导入与角色设置

1. 打开 **Skills → Parser 控制面 → 样本任务**。
2. 创建或打开对应离线样本任务，导入 Q1 与 Q2 两个文件。
3. 对每条命令分别设置样本角色：

   | 周期 | 角色 | `display interface brief` | `display transceiver diagnosis interface` |
   | --- | --- | ---: | ---: |
   | Q1 | 基线/生成样本 | 63 条 | 64 条 |
   | Q2 | 当前/独立验收样本 | 63 条 | 64 条 |

4. 两个文件同名时，界面必须显示周期和采集时间，例如“基线周期 · 采集：…”及“当前周期 · 采集：…”。不要仅凭文件名判断样本身份。
5. 在样本期望中填入记录数。若只核验记录数，`primary_keys` 可以保持为空数组；系统不再把“未提供预期主键集合”误判为主键不匹配。
6. 完成每个命令的样本确认后，点击 **样本已确认，进入语义设计**。

## 4. ParserSpec 设计

### 4.1 接口状态

`display interface brief` 的 ParserSpec 至少应定义：

- record set：`interfaces`
- 业务实体：接口状态
- 主键：`interface`
- 必需字段：`interface`
- 源状态字段：`link`
- 目标候选字段：`interface.oper_status`

H3C 输出中的状态大小写可能不同。TextFSM 必须接受 `UP`、`DOWN`、`ADM`、`Stby` 及其小写形式；Adapter Projection 的值映射采用默认 `casefold`，因此只需要维护一组不重复的源值映射：

```json
{
  "UP": "up",
  "DOWN": "down",
  "ADM": "admin_down",
  "Stby": "unknown"
}
```

不要在同一个 `casefold` 映射中同时填入 `DOWN` 和 `down`。它们属于同一个归一化键，系统会以 fail-closed 方式拒绝重复映射。

### 4.2 光模块诊断

`display transceiver diagnosis interface` 的关键设计为：

```json
{
  "version": 2,
  "business_purpose": "提取 H3C 接口光模块通道、在位状态和收发光功率。",
  "record_sets": [
    {
      "name": "optics",
      "entity": "interface_optical",
      "cardinality": "repeated",
      "primary_keys": ["interface", "channel_token"],
      "fields": {
        "interface": {
          "required": true,
          "type": "interface_name",
          "role": "identity",
          "canonical_candidate": "interface.optical_power.interface"
        },
        "channel_token": {
          "required": false,
          "type": "text",
          "role": "identity",
          "primary_key_default": "aggregate",
          "canonical_candidate": "interface.optical_power.channel"
        },
        "transceiver_present": {
          "required": false,
          "type": "enum",
          "values": ["absent"],
          "role": "state"
        },
        "rx_power_dbm": {"required": false, "type": "number", "role": "metric"},
        "tx_power_dbm": {"required": false, "type": "number", "role": "metric"}
      }
    }
  ]
}
```

`primary_key_default: "aggregate"` 仅用于 Parser 的主键唯一性和样本期望校验。它不会改写 TextFSM 原始记录中的空 `channel_token`，也不会代替 Projection 的标准化职责。

同一条 H3C 命令中的阈值段由第二个 record set `optical_thresholds` 提取，主键为 `interface`，字段为：

- `rx_high_alarm_dbm`、`rx_low_alarm_dbm`
- `tx_high_alarm_dbm`、`tx_low_alarm_dbm`

当前测试数据没有 warning 阈值、模块料号和序列号，因此 Parser 必须保留为空，不能从 alarm 阈值推导 warning，也不能伪造模块身份。

## 5. TextFSM 编辑与本地验证

1. 在 **生成与验证** 中先查看语义字段、冻结样本和每份样本期望。
2. 可使用候选生成；生成模型只能提出 TextFSM 候选，不能创建标准语义，也不能在运行时参与判断。
3. 最多进行三轮生成/修订。未通过时，优先根据逐样本诊断修改 TextFSM 或 ParserSpec。
4. 也可使用 **工程师修订 TextFSM** 直接提交本地模板。工程师修订必须在全部冻结样本上编译、执行并通过兼容性校验。
5. 光模块模板必须覆盖：

   - `Current diagnostic parameters` 的聚合通道；
   - `[channel]` 的编号通道；
   - `The transceiver is absent.` 的无模块接口；
   - 负数 dBm 值。

6. 接口状态模板必须覆盖桥接模式输出中的小写 `down`。本次 Q2 样本中的 `FGE1/0/25 down` 是实际回归样例；若只匹配大写 `DOWN`，会少解析一条记录并被误解释为接口删除。

通过后，界面应提示“工程师修订已通过全样本执行，等待人工语义确认”，候选状态为“待人工确认”。这不是发布成功。

## 6. 人工语义复核与候选状态

1. 在流程第 4 步使用 **前往人工确认 → 审查并确认** 打开审批抽屉。
2. 核对每个冻结样本的：原始/脱敏 CLI 证据、TextFSM、记录表、主键、字段数及质量结果。
3. 手工提交的候选应标识为 **工程师修订**，而不是“平台生成”。
4. 对光模块候选，确认空 `channel_token` 的原始输出仍为空，而审核用主键可以稳定以 `aggregate` 代表该记录。
5. 只有人工确认“记录完整且字段对应正确”和“主键稳定唯一”后，才允许保存语义复核。发布审批仍需由符合职责分离要求的审批人完成。

Parser 与 Projection 仍在各自的人工复核和发布流程中完成；规则链不再等待样本或 TextFSM 才能生成候选。

## 7. Adapter V2 Projection 配置要点

Projection 存在 `CommandAdapter` 的不可变版本 `outputs[].projection` 中，不创建独立 ObservationMapping 表。

### 接口状态 Projection

```text
source_record_set: interfaces
interface   <- source: interface
oper_status <- source: link
              value_mapping: UP/up, DOWN/down, ADM/admin_down, Stby/unknown
              on_unmapped: reject
```

上述的 `UP/up` 是映射效果描述；配置中保持一份大写源键并使用默认 `casefold` 模式。

### 光模块 Projection

```text
source_record_set: optics
interface           <- interface
channel             <- channel_token; "" 或 Current 映射为 aggregate
transceiver_present <- transceiver_present; "" 映射为 present，absent 映射为 absent
rx_power_dbm        <- rx_power_dbm; canonical_unit: dbm; dimensions.channel <- channel
tx_power_dbm        <- tx_power_dbm; canonical_unit: dbm; dimensions.channel <- channel

record_set_joins:
  optics.interface = optical_thresholds.interface
  rx_high_alarm_dbm <- optical_thresholds.rx_high_alarm_dbm
  rx_low_alarm_dbm  <- optical_thresholds.rx_low_alarm_dbm
  tx_high_alarm_dbm <- optical_thresholds.tx_high_alarm_dbm
  tx_low_alarm_dbm  <- optical_thresholds.tx_low_alarm_dbm
```

`record_set_joins` 只允许在同一个 ParserBundle、同一次命令输出内做精确等值连接，不支持跨命令拼接。连接键缺失、重复或必需记录未命中时产生 `partial`/错误结果。一个命令可以输出多个 CheckItem；未映射枚举、非法源字段、空主键或重复主键必须阻断该 Output，不能猜测补齐。

## 8. 本次离线回放结果

使用当前目录中的 Q1/Q2 样本，以只读方式运行：

```powershell
& .\venv\Scripts\python.exe scripts\test\manual\h3c_quarterly_adapter_projection_replay.py `
  --sample-root 'C:\Users\wangd\Desktop\TEXTFSMTEST'
```

预期结果：

| 命令 | Q1 Parser/标准记录 | Q2 Parser/标准记录 | Observation 数 | 确认可见变化 |
| --- | ---: | ---: | ---: | --- |
| `display interface brief` | 63 / 63 | 63 / 63 | 63 / 63 | `FGE1/0/25`：`up → down` |
| `display transceiver diagnosis interface` | 64 / 64，阈值行 16 | 64 / 64，阈值行 16 | 232 / 232 | `Ten-GigabitEthernet1/0/2` 聚合通道：`tx_power_dbm -2.68 → -12.68` |

脚本会编译 TextFSM、校验 Projection 源字段、执行 A→B 映射、生成标准 Observation 并比较两个周期。它不会向 PostgreSQL 写入 Parser、Adapter、Bundle 或 CheckItem 资产。

建议的定向回归命令：

```powershell
& .\venv\Scripts\python.exe -m pytest `
  tests\unit\test_h3c_adapter_projection_replay.py `
  tests\unit\test_parser_runtime_primary_key_defaults.py `
  tests\unit\test_simple_parser_generation.py -q
```

### 8.1 浏览器端到端复核结果

在测试环境通过 **Skills → Parser 控制面 → 样本任务 → 命令资产 → 详情 → 处理 Parser → 审查并确认** 完成了只读复核：

| 命令 | 候选来源 | 样本验收 | 每期记录数 | 结果 |
| --- | --- | --- | ---: | --- |
| `display interface brief` | 工程师修订 | 2 / 2 | 63 | 通过 |
| `display transceiver diagnosis interface` | 工程师修订 | 2 / 2 | 64 | 通过 |

复核时三个“人工语义复核”复选框均保持未选，未点击“保存人工复核”、审批、激活或发布。该步骤验证界面展示和回放证据，不会替代人工语义判断或生产发布。

## 9. 从标准语义 B 生成变化策略与事件规则

1. 打开 **Skills → Parser 控制面 → 生产工作台 → 检查项目录**。
2. 找到目标检查项并点击“编辑检查项”。
3. 在“规则生成依据”中确认：

   - 标准字段和 Observation Type 正确；
   - `state`/`enum` 字段显示正确的 `healthy`、`unhealthy`、`suppressed`、`unknown` 分组；
   - 数值字段的高级契约中已有明确阈值、恢复阈值，或标准阈值字段关系；
   - 页面不再出现要求填写“当前值判断规则 JSON”或“事件规则 JSON”的输入框。

4. 点击 **按当前已发布语义预览规则**。该操作只读取 CheckItem 版本，输入摘要必须显示“0 条样本、0 个 Parser 模板”。
5. 预览成功后点击 **创建规则草稿**。重复点击采用相同摘要的既有草稿，不重复创建版本。
6. 在规则资产审批区继续提交、审批和激活；Runtime Bundle 会优先选择与当前 CheckItem 版本精确匹配的规则资产，只有没有精确资产时才兼容旧的无作用域资产。

本次真实浏览器验证结果：

| CheckItem | 生成结果 | 说明 |
| --- | --- | --- |
| `interface.oper_status` | 1 个 ChangePolicy、2 条 EventRule | `InterfaceDown` 使用 `up -> down/error_disabled/unavailable`；`InterfaceUp` 使用反向恢复集合 |
| `interface.optical_power` v11 | 3 个 ChangePolicy、16 条 EventRule | RX/TX 同时使用设备阈值和 10% 线性功率基线；预览输入为 0 条样本、0 个 Parser 模板 |
| `system.cpu_usage` v9 | 1 个 ChangePolicy、3 条 EventRule | `cpu_usage_percent` 引用 warning/alarm 标准阈值字段，不生成固定 80%/90% |
| `system.memory_usage` v7 | 1 个 ChangePolicy、3 条 EventRule | 仅利用率承担健康判定；used/total 只作证据，不生成健康告警 |

### 9.1 光功率、CPU 和内存的健康判定

三类检查项都使用“当前值与适用于当前实体的阈值”进行比较，不再使用全局固定值：

```text
设备本次输出阈值
    > 设备/型号 Inventory Profile
    > 站点或范围覆盖策略
    > Unknown（本目录未配置全局固定兜底）
```

CPU 标准记录至少应包含 `cpu_usage_percent`，并从设备输出或设备型号策略提供 `cpu_high_warning_percent`、`cpu_high_alarm_percent`。内存对应字段为 `memory_usage_percent`、`memory_high_warning_percent`、`memory_high_alarm_percent`。不同型号可以绑定不同 AssessmentPolicy；例如型号 A 的 warning 为 80%，型号 B 为 95%，运行时会按设备绑定分别解析，不会把二者合并为一个阈值。阈值缺失或同优先级冲突时结果为 Unknown，不判健康也不生成恢复事件。

光功率先检查同一记录中的 warning/alarm 上下限，再检查基线劣化：

```text
baseline_mw = 10 ^ (baseline_dbm / 10)
current_mw  = 10 ^ (current_dbm / 10)
loss_ratio  = (baseline_mw - current_mw) / baseline_mw
```

`loss_ratio > 0.10` 生成基线劣化事件。基线必须与当前记录的 `device + interface + channel + part_number + serial_number` 对应；料号或序列号缺失、模块已更换、无合格基线时均返回 Unknown。模块阈值越过 warning/alarm 时分别生成告警/严重告警，并优先于基线劣化结论。

### 9.2 页面操作检查清单

1. 打开 **生产工作台 → 检查项目录**，确认版本分别为 `interface.optical_power v11`、`system.cpu_usage v9`、`system.memory_usage v7`。
2. 点击检查项编辑按钮，在“规则生成依据”中确认显示“动态阈值”，并核对角色到字段的映射。
3. 光功率还必须显示“线性功率基线”“允许衰减 10%”以及 `part_number + serial_number` 身份字段。
4. 点击 **按当前已发布语义预览规则**。摘要应显示输入中包含 0 条样本、0 个 Parser 模板，证明规则链已与 Parser/TextFSM 解耦。
5. 设备或型号阈值在 **判断策略/AssessmentPolicy** 中按 `inventory_profile` 绑定；只有设备命令能够直接输出阈值时，才由 Adapter 写入同一 Observation 上下文并取得最高优先级。
6. 执行巡检后，在判断依据下钻中核对 `threshold_binding.source_kind` 和证据引用，确认实际选中的是设备输出或目标型号 Profile。

后端接口用于定位问题：

```text
GET  /api/v1/parser/catalog/check-item-versions/{version_id}/rule-context
POST /api/v1/parser/catalog/check-item-versions/{version_id}/rule-candidates
POST /api/v1/parser/catalog/check-item-versions/{version_id}/rule-candidates/drafts
```

返回的 RuleContext 中不得包含 `dataset_ids`、Parser Template ID、Raw CLI、TextFSM 或厂商字段。`context_digest` 相同时，规则候选也必须保持确定性一致。

## 10. 生产切换门槛

以下条件必须同时满足，才能创建新版本并做原子切换：

1. 全部活动结构化检查项都具备 Parser、明确 Projection、绑定 CheckItem 版本和冻结样本回放；覆盖率必须为 100%。
2. 每条样本均能编译、解析、投影和装配新 Runtime Bundle；Parser 或 Projection 失败必须产生数据质量结果，不得推断删除、恢复或告警。
3. Runtime Bundle 不再依赖 `ObservationContract` 运行时桥接；只选择 Parser、Adapter Projection、CheckItem、Current/Change/Event 资产。
4. 人工语义复核和独立发布审批均已完成，且审计摘要一致。
5. 切换后重新采集基线：首个 `complete` 或 `valid_empty` 快照只建基线；`partial`/`unavailable` 不推断删除，也不生成业务 Change/Event。
6. 旧基线不得直接与新链路输出比较。回滚必须选择上一个不可变资产版本，而不是修改已发布版本。

## 11. 常见故障排查

| 现象 | 原因与处理 |
| --- | --- |
| 审批抽屉显示“0/2 个样本通过”，但工程师修订页显示全通过 | 确认运行时已包含 `primary_key_default` 修复；该默认仅用于运行时主键审计，原始空字段必须保留。重启测试服务后重新打开审批抽屉回放。 |
| Q2 `display interface brief` 少一条记录 | 检查 `link` 正则是否接受小写 `down`，并确认 Projection 使用默认 `casefold` 值映射。 |
| Adapter 校验报“duplicate normalized source values” | 不要同时配置 `DOWN` 和 `down`；默认映射模式已不区分大小写。 |
| 光模块空通道被当成主键非法 | 在 `channel_token` 的 ParserSpec 上声明 `primary_key_default: "aggregate"`；不要把它写入 TextFSM 原始结果。 |
| `partial` 或 `unavailable` 后出现接口删除/告警 | 这是不允许的链路行为；检查质量状态门控和基线处理，先修复后再继续验收。 |
| 规则候选接口返回 401 | 登录状态已失效。重新登录后从检查项目录发起操作；不要从旧离线任务 URL 直接重复请求。 |
| 旧 `/adapter-projections/candidates` 请求返回 404 | 旧前端包仍在请求已替换的路由。运行 `start.ps1` 重新构建前端，并从“命令适配器”页面发起 Projection 候选生成。 |
| 提示 Parser template 必须为 active | 这是 A→B 映射链的门槛，与规则链无关。先在命令适配器流程激活 Parser；ChangePolicy/EventRule 应改从检查项目录的标准语义入口生成。 |
| 提示“标准语义还不足以生成完整规则” | 按提示补充对应 Observation Type 的状态分组、静态阈值或标准阈值字段关系；不得用样本统计自动填成业务阈值。 |
| Docker 启动后端失败 | 先确认 Docker Desktop Ready、端口 5432 可监听，再重新运行 `start.ps1`；必要时关闭 Docker AI 后重启 Docker Desktop。 |
