# 非结构化巡检输出对比与报告改造

日期：2026-07-19

## 项目目标

把无法或不适合结构化的巡检输出纳入历史对比和最终报告，同时保持结构化状态、非结构化文本事实和故障事件三种语义严格分离。

```text
Raw CLI
  -> Command Policy 判定 comparison_profile
  -> 去传输噪声、脱敏、规范化
  -> 配置有序行差异 / 日志滚动窗口差异
  -> Change（文本事实）
  -> 技术报告完整差异 + 领导报告摘要
```

非结构化输出发生变化只说明文本事实变化，不自动等同于设备故障。只有经过 active EventRule 判定的结构化 Change 才能形成确定性故障或恢复事件。

## 适用范围

### 配置输出

`display current-configuration`、`show running-config` 等命令采用 `configuration` profile：

- 保留行顺序并比较规范化后的有效配置行。
- 过滤分页、回显和采集时间等传输噪声。
- 对密码、用户、IP、MAC 等字段执行不可逆摘要或占位脱敏。
- 输出新增行数、删除行数、变化分类和受限安全预览。

### 设备日志窗口

`display logbuffer level N`、`show logging` 等命令采用 `log_window` profile：

- 去除时间戳和滚动计数噪声后进行多重集比较。
- 区分本周期新增日志与因窗口滚动而移出的旧日志。
- 报告优先展示新增日志摘要，不把旧日志移出窗口表述为故障恢复。
- 日志变化仅作为调查线索，必须结合结构化状态或人工复核判断影响。

### 明确不适用

- 被 policy 标记为 structured 的命令解析失败时，不允许静默降级为 raw diff。
- Parser 缺失或解析失败必须保留原始质量状态，避免用文本变化掩盖结构化资产缺口。
- 未配置 comparison profile 的 raw 输出不进入正式变化结论。

## 数据与证据契约

每条非结构化 Change 保存：

- 前后 Snapshot ID 与不可变 raw hash。
- comparison profile、版本和规范化摘要。
- 新增/删除行数、相似度和变化分类。
- 经过全局脱敏与长度限制的预览。
- `unstructured_output_changed` 或 `unstructured_noise_only` 决策原因。

原始 CLI 继续作为受控证据交付物保存，领导报告不展开长日志正文。技术报告可以展示脱敏预览，并链接到有权限控制的原始输出。

## 报告语义

领导报告分开呈现：

1. 当前网络风险：来自规则判定后的独立故障事实。
2. 状态变化：来自结构化 Observation 和 Change。
3. 非结构化输出变化：配置或日志的文本事实变化，待复核。
4. 数据可信度：Parser 缺失、解析失败、阈值不完整和设备覆盖情况。

报告不得把以下两种情况写成“未发现异常”：

- 缺少 Parser、Contract、Policy 或 Rule，导致无法判断。
- 光模块不存在、DOM 不支持或没有可靠阈值，导致阈值未知。

## 实施结果

使用 `lixian/Q2` 和 `lixian/Q3` 的 6 台真实设备数据完成回放：

- Q2、Q3 分别采集 36 项可治理的非结构化输出。
- Q2 到 Q3 检测到 16 项有效变化，20 项规范化后未变化。
- 变化包含 2 项配置输出和 14 项设备日志窗口。
- 差异共包含新增 500 行、删除 475 行。
- 领导报告只展示 8 项重点摘要，其余变化保留在技术报告和证据 JSON。
- 安全扫描未发现真实用户名、设备管理 IP 或未脱敏凭据进入报告预览。

## 工程门禁

- comparison profile 由版本化 Command Policy 驱动，不在 Change Detector 中硬编码设备型号。
- 规范化后无有效差异时不创建 Change，并记录 `unstructured_noise_only`。
- Change Detector 只处理 policy 明确授权的 raw comparison。
- 报告 JSON 使用严格序列化，禁止 NaN/Infinity 进入交付物。
- 单元测试覆盖配置差异、滚动日志、噪声抑制、脱敏、报告 Markdown/HTML/XLSX/DOCX。
- 真实 Q2/Q3 回放和 A4 逐页视觉检查作为预发布验收。

## 后续生产化

- 在离线巡检任务详情展示非结构化比较数量、变化类型和质量原因。
- 原始输出下载继续受任务权限控制，不把长日志直接塞入批次列表。
- 对高价值日志类别逐步补 EventRule；在规则审批前仍保持“文本变化待复核”。
- 持续处理 Q2 到 Q3 中 10 个 `template_missing` 和 4 个 `parse_failed`，报告可信度随质量门禁动态更新。
